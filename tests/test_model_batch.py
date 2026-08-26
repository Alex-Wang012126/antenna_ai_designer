import json
import tempfile
import unittest
from pathlib import Path

from config import Config, config_from_model_yaml, infer_model_api_style
from model_batch import ModelBatchSpec
from model_client import (
    AnthropicModelClient,
    OpenAIModelClient,
    ResponsesModelClient,
    create_model_client,
)


class ModelBatchConfigTests(unittest.TestCase):
    @staticmethod
    def _write_yaml(path: Path, model_name: str, import_path: str) -> None:
        path.write_text(
            "\n".join(
                [
                    "job_name: test-job",
                    "agents:",
                    "  - name: test-agent",
                    f"    model_name: {model_name}",
                    "    kwargs:",
                    f'      llm_import_path: "{import_path}"',
                    '      api_base: "https://example.invalid/v1"',
                    '      api_key: "secret-for-test"',
                    "      max_tokens: 12345",
                ]
            ),
            encoding="utf-8",
        )

    def test_batch_preserves_declared_order_and_public_snapshot_has_no_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "first.yaml"
            second = root / "second.yaml"
            self._write_yaml(first, "first-model", "provider.responses:Client")
            self._write_yaml(second, "second-model", "provider.claude:Client")
            batch_file = root / "models.json"
            batch_file.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "models": [
                            {"id": "first", "config_file": first.name},
                            {
                                "id": "second",
                                "config_file": second.name,
                                "api_style": "anthropic_messages",
                                "enabled": True,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            batch = ModelBatchSpec.load(batch_file)

            self.assertEqual([entry.model_id for entry in batch.entries], ["first", "second"])
            self.assertEqual(batch.entries[0].config_file, first.resolve())
            self.assertNotIn("secret-for-test", json.dumps(batch.public_snapshot()))

    def test_duplicate_model_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_file = root / "model.yaml"
            self._write_yaml(config_file, "model", "provider.responses:Client")
            batch_file = root / "models.json"
            batch_file.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "models": [
                            {"id": "duplicate", "config_file": config_file.name},
                            {"id": "duplicate", "config_file": config_file.name},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "模型 id 重复"):
                ModelBatchSpec.load(batch_file)

    def test_yaml_profiles_select_the_expected_client(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cases = [
                ("responses.yaml", "provider.responses:Client", ResponsesModelClient),
                ("claude.yaml", "provider.claude:Client", AnthropicModelClient),
                ("chat.yaml", "provider.kimi:Client", OpenAIModelClient),
            ]
            for filename, import_path, expected_class in cases:
                config_file = root / filename
                self._write_yaml(config_file, filename.removesuffix(".yaml"), import_path)
                config = config_from_model_yaml(Config(), config_file)
                self.assertIsInstance(create_model_client(config), expected_class)
                self.assertEqual(config.model_max_completion_tokens, 12345)
                self.assertEqual(config.model_token_parameter, "max_tokens")

    def test_api_style_inference(self):
        self.assertEqual(infer_model_api_style("vendor.GPTResponses"), "responses")
        self.assertEqual(infer_model_api_style("vendor.claude:Client"), "anthropic_messages")
        self.assertEqual(infer_model_api_style("vendor.gemini:Client"), "chat_completions")


class AnthropicAdapterTests(unittest.TestCase):
    def test_converts_function_call_and_result_to_anthropic_blocks(self):
        system, messages = AnthropicModelClient._convert_messages(
            [
                {"role": "system", "content": "system contract"},
                {"role": "user", "content": "start"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "create_patch_antenna",
                                "arguments": '{"patch_length_mm": 38.4}',
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_1",
                    "name": "create_patch_antenna",
                    "content": '{"success": true}',
                },
            ]
        )

        self.assertEqual(system, "system contract")
        self.assertEqual(messages[1]["content"][0]["type"], "tool_use")
        self.assertEqual(messages[2]["content"][0]["type"], "tool_result")
        self.assertEqual(messages[2]["content"][0]["tool_use_id"], "call_1")

    def test_preserves_anthropic_thinking_blocks_for_the_next_request(self):
        raw_blocks = [
            {"type": "thinking", "thinking": "summary", "signature": "signature"},
            {
                "type": "tool_use",
                "id": "provider_call_1",
                "name": "create_patch_antenna",
                "input": {"patch_length_mm": 38.4},
            },
        ]
        _, messages = AnthropicModelClient._convert_messages(
            [
                {"role": "user", "content": "start"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "provider_call_1",
                            "type": "function",
                            "function": {
                                "name": "create_patch_antenna",
                                "arguments": '{"patch_length_mm": 38.4}',
                            },
                        }
                    ],
                },
            ],
            {"provider_call_1": raw_blocks},
        )

        self.assertEqual(messages[1]["content"], raw_blocks)


if __name__ == "__main__":
    unittest.main()
