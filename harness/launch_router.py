"""Routes one parsed launch to the thing that runs it.

Producers and reviews of real artifacts go to the Dispatcher. The writer, and the review of a
writer draft, go to the writer stub until step 5 builds the real writer (the only place that
changes then is this routing).
"""
from harness.launch_tools import Launch
from harness.store import REVIEWER
from harness.writer_stub import WRITER


class LaunchRouter:
    def __init__(self, dispatcher, writer):
        self._dispatcher, self._writer = dispatcher, writer

    def execute(self, launch: Launch):
        if launch.agent == WRITER:
            return self._writer.write(launch.terminal_state, launch.feedback)
        if launch.agent == REVIEWER and launch.target == WRITER:
            return self._writer.review()
        return self._dispatcher.dispatch(launch.agent, target=launch.target, feedback=launch.feedback)
