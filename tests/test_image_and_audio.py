import unittest
from fastapi.testclient import TestClient

from agy_proxy.models import (
    CLOUDCODE_PLACEHOLDER_MAP,
    PROXY_CATALOG_MODELS,
    normalize_model_name,
    is_image_model,
    is_audio_model,
)
from agy_proxy.server import create_app
from agy_proxy.experiments import enhance_available_models
from agy_proxy.converters.streaming import parse_gemini_sse_candidate


class TestImageAndAudioCapabilities(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = TestClient(self.app)

    def test_image_and_audio_model_normalization(self):
        # Image model aliases
        self.assertEqual(normalize_model_name("gemini-3.1-flash-image"), "gemini-3.1-flash-image")
        self.assertEqual(normalize_model_name("imagen"), "gemini-3.1-flash-image")
        self.assertEqual(normalize_model_name("imagen-3"), "gemini-3.1-flash-image")
        self.assertEqual(normalize_model_name("dall-e-3"), "gemini-3.1-flash-image")
        self.assertEqual(normalize_model_name("dall-e"), "gemini-3.1-flash-image")
        self.assertEqual(normalize_model_name("image"), "gemini-3.1-flash-image")

        # Audio model aliases
        self.assertEqual(normalize_model_name("models/proactive-observer-v10"), "models/proactive-observer-v10")
        self.assertEqual(normalize_model_name("proactive-observer-v10"), "models/proactive-observer-v10")
        self.assertEqual(normalize_model_name("whisper-1"), "models/proactive-observer-v10")
        self.assertEqual(normalize_model_name("whisper"), "models/proactive-observer-v10")

        # Helper predicates
        self.assertTrue(is_image_model("gemini-3.1-flash-image"))
        self.assertTrue(is_image_model("dall-e-3"))
        self.assertTrue(is_image_model("imagen-3"))
        self.assertFalse(is_image_model("gemini-3.8-flash-high"))

        self.assertTrue(is_audio_model("whisper-1"))
        self.assertTrue(is_audio_model("models/proactive-observer-v10"))
        self.assertFalse(is_audio_model("gemini-3.8-flash-high"))

    def test_cloudcode_authentic_placeholders(self):
        # 1. Image model authentic placeholder
        self.assertEqual(CLOUDCODE_PLACEHOLDER_MAP["gemini-3.1-flash-image"], "MODEL_PLACEHOLDER_M21")
        self.assertEqual(normalize_model_name("MODEL_PLACEHOLDER_M21"), "gemini-3.1-flash-image")
        self.assertEqual(normalize_model_name(1021), "gemini-3.1-flash-image")
        self.assertEqual(normalize_model_name("1021"), "gemini-3.1-flash-image")

        # 2. Flash-Lite authentic placeholder
        self.assertEqual(CLOUDCODE_PLACEHOLDER_MAP["gemini-3.1-flash-lite"], "MODEL_PLACEHOLDER_M50")
        self.assertEqual(normalize_model_name("MODEL_PLACEHOLDER_M50"), "gemini-3.1-flash-lite")
        self.assertEqual(normalize_model_name(1050), "gemini-3.1-flash-lite")

        # 3. Gemini 3.5 Flash-Lite authentic placeholder
        self.assertEqual(CLOUDCODE_PLACEHOLDER_MAP["gemini-3.5-flash-lite"], "MODEL_PLACEHOLDER_M198")
        self.assertEqual(normalize_model_name("MODEL_PLACEHOLDER_M198"), "gemini-3.5-flash-lite")
        self.assertEqual(normalize_model_name(1198), "gemini-3.5-flash-lite")

        # 4. Tab completion authentic placeholders
        self.assertEqual(CLOUDCODE_PLACEHOLDER_MAP["tab_flash_lite_preview"], "MODEL_PLACEHOLDER_M19")
        self.assertEqual(CLOUDCODE_PLACEHOLDER_MAP["tab_jump_flash_lite_preview"], "MODEL_PLACEHOLDER_M28")
        self.assertEqual(CLOUDCODE_PLACEHOLDER_MAP["chat_20706"], "MODEL_CHAT_20706")
        self.assertEqual(CLOUDCODE_PLACEHOLDER_MAP["chat_23310"], "MODEL_CHAT_23310")

        # 5. Gemini 2.5 authentic placeholders
        self.assertEqual(CLOUDCODE_PLACEHOLDER_MAP["gemini-2.5-pro"], "MODEL_GOOGLE_GEMINI_2_5_PRO")
        self.assertEqual(CLOUDCODE_PLACEHOLDER_MAP["gemini-2.5-flash"], "MODEL_GOOGLE_GEMINI_2_5_FLASH")
        self.assertEqual(CLOUDCODE_PLACEHOLDER_MAP["gemini-2.5-flash-lite"], "MODEL_GOOGLE_GEMINI_2_5_FLASH_LITE")
        self.assertEqual(CLOUDCODE_PLACEHOLDER_MAP["gemini-2.5-flash-thinking"], "MODEL_GOOGLE_GEMINI_2_5_FLASH_THINKING")

        # 6. Legacy reverse mappings backward compatibility
        self.assertEqual(normalize_model_name("MODEL_PLACEHOLDER_M521"), "gemini-3.8-flash-tiered")
        self.assertEqual(normalize_model_name("MODEL_PLACEHOLDER_M530"), "gemini-3.5-flash-lite")
        self.assertEqual(normalize_model_name("MODEL_PLACEHOLDER_M534"), "gemini-3.1-flash-lite")
        self.assertEqual(normalize_model_name("MODEL_PLACEHOLDER_M548"), "gemini-3.1-flash-image")

    def test_proxy_catalog_contains_all_specialized_models(self):
        self.assertIn("gemini-3.1-flash-image", PROXY_CATALOG_MODELS)
        self.assertIn("chat_20706", PROXY_CATALOG_MODELS)
        self.assertIn("chat_23310", PROXY_CATALOG_MODELS)
        self.assertIn("tab_flash_lite_preview", PROXY_CATALOG_MODELS)
        self.assertIn("tab_jump_flash_lite_preview", PROXY_CATALOG_MODELS)
        self.assertIn("models/proactive-observer-v10", PROXY_CATALOG_MODELS)
        self.assertIn("dall-e-3", PROXY_CATALOG_MODELS)
        self.assertIn("whisper-1", PROXY_CATALOG_MODELS)

    def test_openai_image_generation_endpoint(self):
        # 1. Prompt is required
        resp = self.client.post("/v1/images/generations", json={})
        self.assertEqual(resp.status_code, 400)

        # 2. Successful image generation (b64_json format)
        resp = self.client.post(
            "/v1/images/generations",
            json={
                "prompt": "a cybernetic neon dragonfly",
                "model": "gemini-3.1-flash-image",
                "response_format": "b64_json",
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("created", data)
        self.assertIn("data", data)
        self.assertGreaterEqual(len(data["data"]), 1)
        self.assertIn("b64_json", data["data"][0])

        # 3. Successful image generation (url format)
        resp_url = self.client.post(
            "/v1/images/generations",
            json={
                "prompt": "sunset over mountains",
                "model": "dall-e-3",
                "response_format": "url",
            },
        )
        self.assertEqual(resp_url.status_code, 200)
        data_url = resp_url.json()
        self.assertIn("url", data_url["data"][0])

    def test_openai_image_edit_endpoint(self):
        # 1. Prompt is required
        resp = self.client.post("/v1/images/edits", json={})
        self.assertEqual(resp.status_code, 400)

        # 2. JSON edit request with base64 image
        dummy_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        resp = self.client.post(
            "/v1/images/edits",
            json={
                "image": dummy_b64,
                "prompt": "add a red hat",
                "model": "gemini-3.1-flash-image",
                "response_format": "b64_json",
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("data", data)
        self.assertIn("b64_json", data["data"][0])

        # 3. Multipart edit request with multiple images
        files = [
            ("image", ("test1.png", b"\x89PNG\r\n\x1a\nfakeimagebytes1", "image/png")),
            ("image", ("test2.png", b"\x89PNG\r\n\x1a\nfakeimagebytes2", "image/png")),
        ]
        data_form = {
            "prompt": "combine the two images into one scene",
            "model": "gemini-3.1-flash-image",
            "response_format": "url",
        }
        resp_mp = self.client.post("/v1/images/edits", data=data_form, files=files)
        self.assertEqual(resp_mp.status_code, 200)
        data_mp = resp_mp.json()
        self.assertIn("url", data_mp["data"][0])

        # 4. JSON edit request with multiple images list
        resp_multi = self.client.post(
            "/v1/images/edits",
            json={
                "images": [dummy_b64, dummy_b64],
                "prompt": "fuse kitten into couple picture",
                "model": "gemini-3.1-flash-image",
                "response_format": "b64_json",
            },
        )
        self.assertEqual(resp_multi.status_code, 200)
        data_multi = resp_multi.json()
        self.assertIn("b64_json", data_multi["data"][0])

    def test_openai_audio_transcription_endpoint(self):
        resp = self.client.post(
            "/v1/audio/transcriptions",
            content=b"test-audio-bytes-data",
            headers={"Content-Type": "audio/wav"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("text", data)

    def test_enhance_available_models_metadata_groups(self):
        initial_data = {
            "models": {
                "gemini-3.8-flash-high": {"displayName": "Gemini 3.8 Flash"},
            },
            "tieredModelIds": {
                "flashLite": ["some-old-model"],
                "flash": ["some-old-model"],
                "pro": ["some-old-model"],
            },
        }
        enhanced = enhance_available_models(initial_data)

        # imageGenerationModelIds
        self.assertIn("imageGenerationModelIds", enhanced)
        self.assertIn("gemini-3.1-flash-image", enhanced["imageGenerationModelIds"])

        # audioTranscriptionModelIds
        self.assertIn("audioTranscriptionModelIds", enhanced)
        self.assertIn("models/proactive-observer-v10", enhanced["audioTranscriptionModelIds"])

        # webSearchModelIds
        self.assertIn("webSearchModelIds", enhanced)
        self.assertEqual(enhanced["webSearchModelIds"], ["gemini-3.5-flash-lite"])

        # tieredModelIds
        self.assertEqual(enhanced["tieredModelIds"]["flashLite"], ["gemini-3.5-flash-lite"])
        self.assertEqual(enhanced["tieredModelIds"]["flash"], ["gemini-3.8-flash-tiered"])
        self.assertEqual(enhanced["tieredModelIds"]["pro"], ["gemini-3.8-flash-tiered"])

    def test_parse_gemini_sse_candidate_with_inline_image(self):
        cand = {
            "content": {
                "role": "model",
                "parts": [
                    {"text": "Here is your generated image:"},
                    {
                        "inlineData": {
                            "mimeType": "image/jpeg",
                            "data": "aW1hZ2VkYXRh",
                        }
                    },
                ],
            },
            "finishReason": "STOP",
        }
        text, thought, tool_calls, finish_reason, _ = parse_gemini_sse_candidate(cand)
        self.assertIn("Here is your generated image:", text)
        self.assertIn("![Generated Image](data:image/jpeg;base64,aW1hZ2VkYXRh)", text)
        self.assertEqual(finish_reason, "STOP")

        # Also test snake_case inline_data
        cand_snake = {
            "content": {
                "role": "model",
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": "image/png",
                            "data": "cG5nZGF0YQ==",
                        }
                    }
                ],
            }
        }
        text_s, _, _, _, _ = parse_gemini_sse_candidate(cand_snake)
        self.assertIn("![Generated Image](data:image/png;base64,cG5nZGF0YQ==)", text_s)

    def test_image_model_payload_generation(self):
        from agy_proxy.converter import openai_to_cloudcode_payload, anthropic_to_cloudcode_payload
        from agy_proxy.models import OpenAIChatRequest, AnthropicRequest

        # OpenAI format -> CloudCode payload
        req_oa = OpenAIChatRequest(
            model="gemini-3.1-flash-image",
            messages=[{"role": "user", "content": "Generate a mountain sunset"}],
        )
        payload_oa = openai_to_cloudcode_payload(req_oa, project_id="test-proj")
        self.assertEqual(payload_oa["requestType"], "image_gen")
        self.assertEqual(payload_oa["model"], "gemini-3.1-flash-image")
        self.assertTrue(payload_oa["requestId"].startswith("image_gen/"))
        self.assertEqual(payload_oa["request"]["generationConfig"]["candidateCount"], 1)
        self.assertEqual(payload_oa["request"]["generationConfig"]["imageConfig"]["aspectRatio"], "4:3")
        self.assertNotIn("tools", payload_oa["request"])
        self.assertNotIn("systemInstruction", payload_oa["request"])

        # OpenAI format -> CloudCode payload with explicit aspect ratio in prompt
        req_oa_ratio = OpenAIChatRequest(
            model="gemini-3.1-flash-image",
            messages=[{"role": "user", "content": "Generate a 16:9 wallpaper of starry night"}],
        )
        payload_oa_ratio = openai_to_cloudcode_payload(req_oa_ratio, project_id="test-proj")
        self.assertEqual(payload_oa_ratio["request"]["generationConfig"]["imageConfig"]["aspectRatio"], "16:9")

        # OpenAI format with size parameter
        req_oa_size = OpenAIChatRequest(
            model="gemini-3.1-flash-image",
            messages=[{"role": "user", "content": "Draw an avatar"}],
            size="1024x1024",
        )
        payload_oa_size = openai_to_cloudcode_payload(req_oa_size, project_id="test-proj")
        self.assertEqual(payload_oa_size["request"]["generationConfig"]["imageConfig"]["aspectRatio"], "1:1")

        # Anthropic format -> CloudCode payload
        req_ant = AnthropicRequest(
            model="gemini-3.1-flash-image",
            messages=[{"role": "user", "content": "Draw an ocean view vertical story"}],
        )
        payload_ant = anthropic_to_cloudcode_payload(req_ant, project_id="test-proj")
        self.assertEqual(payload_ant["requestType"], "image_gen")
        self.assertEqual(payload_ant["model"], "gemini-3.1-flash-image")
        self.assertTrue(payload_ant["requestId"].startswith("image_gen/"))
        self.assertEqual(payload_ant["request"]["generationConfig"]["candidateCount"], 1)
        self.assertEqual(payload_ant["request"]["generationConfig"]["imageConfig"]["aspectRatio"], "9:16")

    def test_extract_aspect_ratio_all_supported(self):
        from agy_proxy.converters.common import extract_aspect_ratio, SUPPORTED_ASPECT_RATIOS
        self.assertIn("1:1", SUPPORTED_ASPECT_RATIOS)
        self.assertIn("16:9", SUPPORTED_ASPECT_RATIOS)
        self.assertIn("9:16", SUPPORTED_ASPECT_RATIOS)
        self.assertIn("3:4", SUPPORTED_ASPECT_RATIOS)
        self.assertIn("4:3", SUPPORTED_ASPECT_RATIOS)
        self.assertIn("21:9", SUPPORTED_ASPECT_RATIOS)
        self.assertIn("3:2", SUPPORTED_ASPECT_RATIOS)
        self.assertIn("2:3", SUPPORTED_ASPECT_RATIOS)
        self.assertIn("5:4", SUPPORTED_ASPECT_RATIOS)
        self.assertIn("4:5", SUPPORTED_ASPECT_RATIOS)

        self.assertEqual(extract_aspect_ratio("draw a cat"), "4:3")
        self.assertEqual(extract_aspect_ratio("draw 16:9 wallpaper"), "16:9")
        self.assertEqual(extract_aspect_ratio("vertical phone reel"), "9:16")
        self.assertEqual(extract_aspect_ratio("portrait photo"), "3:4")
        self.assertEqual(extract_aspect_ratio("square avatar"), "1:1")
        self.assertEqual(extract_aspect_ratio(size="1024x1024"), "1:1")
        self.assertEqual(extract_aspect_ratio(size="1792x1024"), "16:9")
        self.assertEqual(extract_aspect_ratio(size="1024x1792"), "9:16")
        self.assertEqual(extract_aspect_ratio(explicit_ratio="21:9"), "21:9")


if __name__ == "__main__":
    unittest.main()
