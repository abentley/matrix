import asyncio
import logging
import yaml

from pathlib import Path

from .actions import Actions
from matrix.model import TestFailure
from .plan import generate_plan, validate_plan
from .selectors import Selectors


log = logging.getLogger("glitch")
DEFAULT_PLAN_NAME = "glitch_plan.yaml"


def default_resolver(model, kind, name):
    if kind not in ["application", "unit", "model", "controller", "relation"]:
        return None
    entities = getattr(model, kind + "s")
    obj = entities[name]
    return obj


async def select(rule, model, selectors, objects=None,
                 resolver=default_resolver):
    if not selectors:
        if objects is None:
            raise ValueError('No valid objects specified by selectors')
        return objects

    # if there are string names being passed (from a serialized plan for
    # example) we must resolve them relative to the current model. This is
    # pluggable using a resolver object which takes a model,
    cur = None
    args = [rule, model]
    # This can raise many an exception
    for selector in selectors:
        data = selector.copy()
        m = Selectors.get(data.pop('selector'))
        for k, v in data.items():
            if isinstance(v, str):
                # attempt resolution
                o = resolver(model, k, v)
                if o is not None:
                    data[k] = o

        cur = await m(*args, **data)
        if len(cur) < 1:  # If we get an empty list ...
            return cur  # ... return it, and skip the rest.
        args = [rule, model, cur]
    return cur


async def execute_actions(actions, model, rule, exception, bus=None):
    """Execute a glitch plan.

    :param actions: A list of actions from a glitch plan.
    :param model: A Juju model to apply the actions to.
    :param rule: A model.Rule, typically used for logging.  Passed on to
        Glitch actions.
    :param exception: The exception to raise if errors are encountered.  If
        None, no exception will be raised if errors are encountered.
    :param bus: Bus to send glitch.activate events to.  If None, no events
        will be sent.
    """
    # Execute glitch plan. We perform destructive operations here!
    for action in actions:
        actionf = Actions[action.pop('action')]['func']
        selectors = action.pop('selectors')
        # Find a set of units to act upon
        objects = await select(rule, model, selectors)
        if not objects:
            # If we get an empty set of objects back, just skip this action.
            rule.log.error(
                "Could not run {}. No objects for selectors {}".format(
                    actionf.__name__, selectors))
            continue

        # Run the specified action on those units
        rule.log.info("GLITCHING {}: {}".format(actionf.__name__, objects))

        errors = False
        try:
            await asyncio.wait_for(actionf(rule, model, objects, **action), 30)
        except asyncio.TimeoutError:
            rule.log.error("Timeout running {}".format(actionf.__name__))
            errors = True
        except Exception as e:
            rule.log.exception(
                "Exception while running {}: {} {}.".format(
                    actionf.__name__, type(e), e))
            errors = True
        if errors and exception is not None:
            raise exception

        if bus is not None:
            bus.dispatch(
                origin="glitch",
                payload={'action': actionf.__name__, **action},
                kind="glitch.activate"
            )
        await asyncio.sleep(2, loop=model.loop)


async def glitch(context, rule, task, event=None):
    """
    Perform a set of actions against a model, with a mind toward causing
    trouble.

    The set of actions is defined by a plan, which is either passed in at
    config time, or generated on the fly.

    We write the last plan to be run out to a YAML file.

    """
    rule.log.info("Starting glitch")

    model = context.juju_model
    config = context.config

    glitch_file = None
    if task.args.get('plan'):
        # If the user specifies {bundle}/some/path in matrix config,
        # replace 'bundle' with the path to the bundle.
        glitch_file = Path(task.args['plan'].format(bundle=config.path))
    elif config.glitch_plan:
        glitch_file = Path(config.glitch_plan)

    if glitch_file:
        with glitch_file.open('r') as f:
            glitch_plan = validate_plan(yaml.load(f))
        rule.log.info("loaded glitch plan from {}".format(glitch_file))
    else:
        glitch_plan = await generate_plan(
            rule,
            model,
            num=int(config.glitch_num))
        glitch_plan = validate_plan(glitch_plan)

        if config.output_dir:
            glitch_output = Path(config.output_dir,
                                 config.glitch_output.format(
                                     model_name=model.info.name))
        else:
            glitch_output = Path(config.glitch_output.format(
                model_name=model.info.name))
        rule.log.info("Writing glitch plan to {}".format(glitch_output))
        with glitch_output.open('w') as output_file:
            output_file.write(yaml.dump(glitch_plan))

    if task.gating:
        exception = TestFailure(
                    task, "Exceptions were raised during glitch run.")
    else:
        exception = None
    await execute_actions(glitch_plan['actions'], model, rule, exception,
                          context.bus)
    rule.log.info("Finished glitch")
    return True
