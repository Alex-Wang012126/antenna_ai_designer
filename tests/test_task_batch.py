import json
import tempfile
import unittest
from pathlib import Path

from task_batch import TaskBatchSpec
from task_spec import AntennaTaskSpec, load_default_task


class TaskBatchTests(unittest.TestCase):
    def test_loads_enabled_tasks_in_registry_order(self):
        batch = TaskBatchSpec.load("task_batch.json")
        tasks = batch.load_enabled_tasks()

        self.assertEqual(len(tasks), 3)
        self.assertEqual(tasks[0].task_id, "inset_patch_2p45_v6")
        self.assertEqual(
            batch.public_snapshot()["tasks"][0]["spec_file"],
            str(Path("tasks/inset_patch_2p45.json").resolve()),
        )

    def test_rejects_duplicate_task_ids(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task_file = root / "task.json"
            task_file.write_text(
                json.dumps(load_default_task().to_dict()), encoding="utf-8"
            )
            batch_file = root / "tasks.json"
            batch_file.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "tasks": [
                            {"id": "same", "spec_file": task_file.name},
                            {"id": "same", "spec_file": task_file.name},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "题目 id 重复"):
                TaskBatchSpec.load(batch_file)

    def test_requires_at_least_one_enabled_task(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task_file = root / "task.json"
            task_file.write_text(
                json.dumps(load_default_task().to_dict()), encoding="utf-8"
            )
            batch_file = root / "tasks.json"
            batch_file.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "tasks": [{"id": "task", "spec_file": task_file.name, "enabled": False}],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "至少要启用一个题目"):
                TaskBatchSpec.load(batch_file)


class TopologyFrameworkTests(unittest.TestCase):
    def test_task_selects_its_topology_framework(self):
        task = load_default_task()

        self.assertEqual(task.topology_id, "inset_fed_rectangular_patch")
        self.assertEqual(task.framework.topology_id, task.topology_id)

    def test_unknown_topology_is_isolated_from_patch_framework(self):
        task_data = load_default_task().to_dict()
        task_data["task_id"] = "different_topology"
        task_data["topology"]["id"] = "not_a_patch"

        with self.assertRaisesRegex(ValueError, "unsupported topology.id"):
            AntennaTaskSpec.from_mapping(task_data)


if __name__ == "__main__":
    unittest.main()
