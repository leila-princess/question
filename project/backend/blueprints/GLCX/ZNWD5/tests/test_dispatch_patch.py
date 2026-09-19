"""ZNWD5 dispatch 补丁：pipeline 必须调用 patched stream_route_plan_auto。"""

from __future__ import annotations

import os
import unittest


class TestDispatchPatch(unittest.TestCase):
    def test_pipeline_uses_znwd5_dispatch_after_bootstrap(self):
        os.environ["ZNWD5_ROUTING_MODE"] = "action_planner"
        import blueprints.GLCX.ZNWD4.pipeline as pl4
        import blueprints.GLCX.ZNWD4.routing_dispatcher as rd4

        from blueprints.GLCX.ZNWD5.bootstrap import apply_znwd5_patches

        apply_znwd5_patches()
        self.assertEqual(rd4.stream_route_plan_auto.__name__, "_znwd5_stream_route_plan_auto")
        self.assertIs(pl4.stream_route_plan_auto, rd4.stream_route_plan_auto)
        self.assertEqual(
            pl4.routing_dispatcher.stream_route_plan_auto.__name__,
            "_znwd5_stream_route_plan_auto",
        )


if __name__ == "__main__":
    unittest.main()
