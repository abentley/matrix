import asyncio
from tempfile import NamedTemporaryFile
import unittest
import yaml

from juju.application import Application
from juju.model import Model
from juju.delta import ApplicationDelta, UnitDelta

from matrix import model
from matrix.bus import Bus
from matrix.tasks.glitch.main import glitch


class TestGlitch(unittest.TestCase):

    def test_glitch(self):
        task = model.Task(command='glitch', args={'path': None})
        rule = model.Rule(task)
        loop = asyncio.get_event_loop()
        bus = Bus(loop=loop)
        suite = []

        class config:
            path = None

        context = model.Context(loop, bus, suite, config, None)

        juju_model = Model()
        juju_model.state.apply_delta(ApplicationDelta(
            ('application', 'type2', {'name': 'foo'})))
        juju_model.state.apply_delta(UnitDelta(('unit', 'type1', {
            'name': 'steve',
            'application': 'foo',
            })))

        context.juju_model = juju_model

        plan = {'actions': [{
            'action': 'kill_juju_agent',
            'selectors': [{
                'selector': 'units',
                'application': 'foo',
                }],
            }]}

        with NamedTemporaryFile() as plan_file:
            yaml.safe_dump(plan, plan_file, encoding='utf8')
            task.args['plan'] = plan_file.name
            loop.run_until_complete(glitch(context, rule, task, None))


if __name__ == '__main__':
    unittest.main()
