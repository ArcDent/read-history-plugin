"""`_manifest.json` 的结构断言：字段名、版本区间、能力清单与 i18n。

宿主用强类型模型校验 manifest（`extra="forbid"`），写错字段名或多写字段会直接拒绝
加载。这里在本地复现同一份字面量契约，把 tasks.md 任务 1.1 的验收标准变成可重复的
检查；容器内的 ManifestValidator 校验在部署验证中做，作为最终确认。
"""

from __future__ import annotations

import json
import re
import unittest

from support import PLUGIN_DIR

MANIFEST_PATH = PLUGIN_DIR / "_manifest.json"

PLUGIN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_]+(?:[.-][A-Za-z0-9_]+)+$")
"""宿主对插件 ID 的正则：至少两段，以点号或横线连接。"""

STRICT_VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
"""宿主对 manifest `version` 的要求：严格三段式。"""

REQUIRED_CAPABILITIES = (
    "chat.get_group_streams",
    "chat.get_private_streams",
    "chat.get_stream_by_group_id",
    "chat.get_stream_by_user_id",
    "message.get_by_time_in_chat",
    "message.build_readable",
    "database.get",
    "database.count",
    "llm.generate",
    "component.reload_plugin",
)
"""本插件实际要用到的十个能力，全部来自宿主能力注册表。"""

ALLOWED_TOP_LEVEL_KEYS = frozenset(
    {
        "manifest_version",
        "version",
        "name",
        "description",
        "author",
        "license",
        "urls",
        "host_application",
        "sdk",
        "dependencies",
        "llm_providers",
        "plugin_type",
        "display",
        "capabilities",
        "i18n",
        "id",
    }
)
"""宿主 manifest v2 模型允许的顶层键；多写一个都会被 `extra="forbid"` 拒绝。"""

HTTP_URL_PATTERN = re.compile(r"^https?://\S+$")


class ManifestTests(unittest.TestCase):
    """宿主 manifest v2 的必填字段与取值契约。"""

    @classmethod
    def setUpClass(cls) -> None:
        with MANIFEST_PATH.open("r", encoding="utf-8") as manifest_file:
            cls.manifest = json.load(manifest_file)

    def test_top_level_keys_are_all_known(self) -> None:
        self.assertEqual(set(self.manifest) - ALLOWED_TOP_LEVEL_KEYS, set())

    def test_manifest_version_is_two(self) -> None:
        self.assertEqual(self.manifest["manifest_version"], 2)

    def test_id_has_at_least_two_segments(self) -> None:
        plugin_id = self.manifest["id"]
        self.assertRegex(plugin_id, PLUGIN_ID_PATTERN)
        self.assertGreaterEqual(len(re.split(r"[.-]", plugin_id)), 2)

    def test_version_is_strict_three_part(self) -> None:
        self.assertRegex(self.manifest["version"], STRICT_VERSION_PATTERN)

    def test_name_description_license_are_not_empty(self) -> None:
        for key in ("name", "description", "license"):
            self.assertTrue(str(self.manifest[key]).strip(), f"{key} 不能为空")

    def test_author_has_name_and_http_url(self) -> None:
        author = self.manifest["author"]
        self.assertTrue(str(author["name"]).strip())
        self.assertRegex(author["url"], HTTP_URL_PATTERN)

    def test_urls_repository_is_http_url(self) -> None:
        self.assertRegex(self.manifest["urls"]["repository"], HTTP_URL_PATTERN)

    def test_host_application_range(self) -> None:
        self.assertEqual(
            self.manifest["host_application"],
            {"min_version": "1.3.6", "max_version": "1.99.99"},
        )

    def test_sdk_range(self) -> None:
        self.assertEqual(
            self.manifest["sdk"],
            {"min_version": "2.12.0", "max_version": "2.99.99"},
        )

    def test_declared_capabilities_cover_required_ten(self) -> None:
        declared = self.manifest["capabilities"]
        self.assertEqual(len(declared), len(set(declared)), "能力清单里有重复项")
        self.assertEqual(set(REQUIRED_CAPABILITIES) - set(declared), set())

    def test_i18n_default_locale_is_supported(self) -> None:
        i18n = self.manifest["i18n"]
        self.assertEqual(i18n["default_locale"], "zh-CN")
        self.assertIn("zh-CN", i18n["supported_locales"])

    def test_no_python_dependency_declared(self) -> None:
        self.assertEqual(self.manifest.get("dependencies", []), [])


if __name__ == "__main__":
    unittest.main()
