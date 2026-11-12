import base64
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import urllib.request

import cv2
import numpy as np

from app.config import config_manager
from app.database import Database
from app.homography import extract_spot_patch, subdivide_perspective_row
from app.recommender import ParkingRecommender
from app.server import ParkingAppContext, create_http_server
from app.state_machine import SpotStateMachine
from app.vision_pipeline import VisionPipeline


class CommunityParkingSystemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp_dir.name) / "test_parking.db")
        self.db = Database(self.db_path)
        self.fsm = SpotStateMachine(self.db)
        self.vision = VisionPipeline(self.db, self.fsm)
        self.recommender = ParkingRecommender(self.db)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_perspective_homography_near_far_foreshortening(self) -> None:
        """Near edge is wide (220px), far edge is narrow (90px) -> Homography must produce foreshortened trapezoids."""
        # P0->P3 is near stall edge (height 220), P1->P2 is far stall edge (height 90)
        outer_quad = [[100, 300], [900, 380], [900, 470], [100, 520]]
        traps = subdivide_perspective_row(outer_quad, count=5, direction="along_01")
        self.assertEqual(len(traps), 5)

        first_width = traps[0][1][0] - traps[0][0][0]
        last_width = traps[-1][1][0] - traps[-1][0][0]
        # Because far edge is compressed (90px vs 220px), projective foreshortening makes far stalls narrower in pixels!
        self.assertGreater(first_width, last_width * 1.4)

    def test_irregular_and_elastic_patch_extraction(self) -> None:
        img = np.full((720, 1280, 3), 90, dtype=np.uint8)
        pentagon = [[200, 200], [340, 190], [380, 310], [290, 370], [180, 310]]
        patch_irr, mask_irr = extract_spot_patch(img, pentagon, spot_type="irregular")
        self.assertEqual(patch_irr.shape, (224, 224, 3))
        self.assertEqual(mask_irr.shape, (224, 224))
        # Exterior corners of letterboxed irregular polygon must be masked out (0)
        self.assertEqual(int(mask_irr[0, 0]), 0)

        strip_quad = [[100, 400], [1100, 380], [1120, 540], [90, 560]]
        patch_strip, _ = extract_spot_patch(img, strip_quad, spot_type="elastic_strip")
        self.assertEqual(patch_strip.shape, (160, 672, 3))

    def test_azure_ai_foundry_url_and_auth_resolver(self) -> None:
        # 1. Full Target URI pasted directly from Azure AI Foundry portal
        target_uri = "https://my-foundry.cognitiveservices.azure.com/openai/deployments/gpt-4o/chat/completions?api-version=2025-01-01-preview"
        url1, h1, fb1 = VisionPipeline.resolve_vlm_request_target(
            {"base_url": target_uri, "api_key": "az-secret-key", "model": "gpt-4o", "auth_mode": "auto"}
        )
        self.assertEqual(url1, target_uri)
        self.assertEqual(h1.get("api-key"), "az-secret-key")
        self.assertEqual(fb1.get("Authorization"), "Bearer az-secret-key")

        # 2. Azure OpenAI resource root
        url2, h2, _ = VisionPipeline.resolve_vlm_request_target(
            {
                "base_url": "https://demo-res.openai.azure.com/",
                "api_key": "k2",
                "model": "gpt-4.1-mini",
                "api_version": "2024-12-01-preview",
            }
        )
        self.assertEqual(
            url2,
            "https://demo-res.openai.azure.com/openai/deployments/gpt-4.1-mini/chat/completions?api-version=2024-12-01-preview",
        )
        self.assertEqual(h2.get("api-key"), "k2")

        # 3. Azure AI Foundry Serverless /models endpoint
        url3, h3, _ = VisionPipeline.resolve_vlm_request_target(
            {
                "base_url": "https://proj-eastus.services.ai.azure.com/models",
                "api_key": "k3",
                "model": "Phi-4-multimodal-instruct",
                "api_version": "2024-05-01-preview",
            }
        )
        self.assertEqual(
            url3,
            "https://proj-eastus.services.ai.azure.com/models/chat/completions?api-version=2024-05-01-preview",
        )
        self.assertEqual(h3.get("api-key"), "k3")

    def test_dual_frame_debounce_filters_passing_car(self) -> None:
        spots = self.db.list_spots(camera_id="CAM_3F_WIN")
        sp0 = next(s for s in spots if s["spot_id"] == "B3-01")
        self.assertEqual(sp0["status"], "FREE")

        # Frame 1 at t=1000: passing car briefly occludes B3-01 -> enters PENDING_OCCUPIED, stays FREE!
        r1 = self.fsm.apply_observation(
            sp0, "OCCUPIED", 0, 1, 0.91, "疑似车辆", "Fast", debounce_seconds=8.0, now=1000.0
        )
        self.assertEqual(r1["transition"], "ENTERED_PENDING")
        self.assertEqual(r1["committed_status"], "FREE")

        # Frame 2a at t=1004 (car drove past, spot is empty again) -> reverts transient!
        sp0_after = next(s for s in self.db.list_spots(camera_id="CAM_3F_WIN") if s["spot_id"] == "B3-01")
        r2 = self.fsm.apply_observation(
            sp0_after, "FREE", 1, 0, 0.95, "路面空闲", "Fast", debounce_seconds=8.0, now=1004.0
        )
        self.assertEqual(r2["transition"], "REVERTED_TRANSIENT")
        self.assertEqual(r2["committed_status"], "FREE")

        # Now real car parks: Frame 1 at t=2000 -> Frame 2 at t=2009.5 (>=8s) -> COMMITTED_DEBOUNCED!
        sp0_clean = next(s for s in self.db.list_spots(camera_id="CAM_3F_WIN") if s["spot_id"] == "B3-01")
        self.fsm.apply_observation(
            sp0_clean, "OCCUPIED", 0, 1, 0.92, "停入车辆", "Fast", debounce_seconds=8.0, now=2000.0
        )
        sp0_pend = next(s for s in self.db.list_spots(camera_id="CAM_3F_WIN") if s["spot_id"] == "B3-01")
        r_commit = self.fsm.apply_observation(
            sp0_pend, "OCCUPIED", 0, 1, 0.94, "停入车辆稳定", "Fast", debounce_seconds=8.0, now=2009.5
        )
        self.assertEqual(r_commit["transition"], "COMMITTED_DEBOUNCED")
        self.assertEqual(r_commit["committed_status"], "OCCUPIED")

    def test_soft_lock_dispatches_concurrent_neighbors_and_suv_filter(self) -> None:
        # Bldg 5-1 has 1 isolated spot right downstairs (Z_BLDG5_DEADEND, 20m walk, +42m dead-end penalty -> score 48)
        # vs Z_BLDG3_EAST (62m walk + 1.75 penalty - 42 abundance bonus = 21.8 -> safer!)
        # Let's test locking when only 1 spot remains in Z_BLDG5_DEADEND vs sequential locking
        rec1 = self.recommender.recommend("3-2", vehicle_type="sedan", driver_tag="CarA", now=5000.0)
        self.assertTrue(rec1["has_recommendation"])
        self.assertEqual(rec1["primary"]["zone_id"], "Z_BLDG3_EAST")
        initial_eff = rec1["primary"]["effective_free"]

        # Second car immediately queries -> effective_free in Z_BLDG3_EAST must be reduced by 1 due to 180s soft lock!
        rec2 = self.recommender.recommend("3-2", vehicle_type="sedan", driver_tag="CarB", now=5015.0)
        self.assertEqual(rec2["primary"]["effective_free"], initial_eff - 1)

        # SUV filter: Z_BLDG3_EAST has 1 compact_only spot (B3-06-IRR), so SUV sees 1 fewer raw_free spot than sedan
        zones_sedan = {z["zone_id"]: z for z in self.recommender.get_zone_summaries("sedan", now=6000.0)}
        zones_suv = {z["zone_id"]: z for z in self.recommender.get_zone_summaries("suv", now=6000.0)}
        self.assertEqual(
            zones_sedan["Z_BLDG3_EAST"]["raw_free"] - zones_suv["Z_BLDG3_EAST"]["raw_free"], 1
        )

    def test_e2e_http_snapshot_zero_disk_and_azure_vlm_mock(self) -> None:
        ctx = ParkingAppContext(database=self.db)
        srv = create_http_server(host="127.0.0.1", port=0, ctx=ctx)
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()

        try:
            # Configure mock Azure AI Foundry endpoint in always_vlm mode
            config_manager.update(
                {
                    "vlm": {
                        "mode": "always_vlm",
                        "base_url": "https://mock-foundry.openai.azure.com",
                        "api_key": "test-azure-key",
                        "model": "gpt-4o",
                    }
                }
            )

            # Create synthetic 1280x720 JPEG in memory
            canvas = np.full((720, 1280, 3), 75, dtype=np.uint8)
            ok, jpg_buf = cv2.imencode(".jpg", canvas)
            self.assertTrue(ok)
            b64_img = "data:image/jpeg;base64," + base64.b64encode(jpg_buf.tobytes()).decode("ascii")

            mock_http_resp = MagicMock()
            mock_http_resp.status_code = 200
            mock_http_resp.ok = True
            mock_http_resp.json.return_value = {
                "choices": [
                    {
                        "message": {
                            "content": '{"status":"BLOCKED","confidence":0.97,"reason":"雪糕桶占道"}'
                        }
                    }
                ]
            }

            with patch("app.vision_pipeline.requests.post", return_value=mock_http_resp) as mock_post:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/cameras/CAM_SOUTH_01/snapshot",
                    data=json.dumps(
                        {"image_base64": b64_img, "instant_confirm": True, "force_reanalyze": True}
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    result = json.loads(resp.read().decode("utf-8"))

                self.assertEqual(result["processed_spots"], 4)
                self.assertFalse(result["raw_image_retained_on_disk"])
                self.assertEqual(ctx.vision.privacy_stats["raw_images_written_to_disk"], 0)
                self.assertGreaterEqual(mock_post.call_count, 4)
                self.assertTrue(all(r["fsm"]["committed_status"] == "BLOCKED" for r in result["reports"]))
        finally:
            config_manager.update({"vlm": {"mode": "hybrid", "base_url": "", "clear_api_key": True}})
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
