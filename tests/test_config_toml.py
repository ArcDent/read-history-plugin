"""`config.toml` 的默认值断言（tasks.md 任务 1.2 验收标准的字面量）。

随插件发布的默认配置必须与 `config.py` 的模型默认值一致，且 `[plugin] config_version`
必须存在（宿主缺它会拒绝加载配置）。这里用 `tomllib` 直接读文件，不 import 插件模块，
所以本机任意 Python 3.11+ 都能跑。
"""

from __future__ import annotations

import unittest

from support import PLUGIN_DIR

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 及更早
    tomllib = None


@unittest.skipIf(tomllib is None, "读取 TOML 需要 Python 3.11+ 的 tomllib")
class ConfigTomlDefaultsTests(unittest.TestCase):
    """默认配置的四节取值。"""

    @classmethod
    def setUpClass(cls) -> None:
        with (PLUGIN_DIR / "config.toml").open("rb") as config_file:
            cls.config = tomllib.load(config_file)

    def test_plugin_section_defaults(self) -> None:
        self.assertIs(self.config["plugin"]["enabled"], False)
        self.assertTrue(str(self.config["plugin"]["config_version"]).strip())

    def test_access_section_defaults(self) -> None:
        self.assertEqual(self.config["access"]["readable_streams"], [])
        self.assertIs(self.config["access"]["private_enabled"], False)

    def test_recent_section_defaults(self) -> None:
        self.assertEqual(self.config["recent"]["default_limit"], 20)
        self.assertEqual(self.config["recent"]["max_limit"], 50)
        self.assertIs(self.config["recent"]["include_bot_messages"], False)

    def test_summary_section_defaults(self) -> None:
        self.assertEqual(self.config["summary"]["window_hours"], 6)
        self.assertEqual(self.config["summary"]["max_input_messages"], 200)

    def test_no_unexpected_sections_or_keys(self) -> None:
        expected = {
            "plugin": {"enabled", "config_version"},
            "access": {"readable_streams", "private_enabled"},
            "recent": {"default_limit", "max_limit", "include_bot_messages"},
            "summary": {"window_hours", "max_input_messages"},
        }
        self.assertEqual(set(self.config), set(expected))
        for section, keys in expected.items():
            self.assertEqual(set(self.config[section]), keys, section)


if __name__ == "__main__":
    unittest.main()
