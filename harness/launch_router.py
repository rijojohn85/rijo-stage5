"""Routes one parsed launch to the dispatcher, which runs every agent: producers, the reviewer and the writer."""
from harness.launch_tools import Launch


class LaunchRouter:
    def __init__(self, dispatcher):
        self._dispatcher = dispatcher

    def execute(self, launch: Launch):
        return self._dispatcher.dispatch(launch.agent, target=launch.target, feedback=launch.feedback,
                                         terminal_state=launch.terminal_state)
