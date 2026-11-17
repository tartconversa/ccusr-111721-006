import json
import tempfile
import unittest
from pathlib import Path

from exchange import Exchange
from exchange.errors import IdentityError


class IdentityTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = Exchange(Path(self.temp.name) / "exchange.db")
        self.service.catalog.register("flow", "机构甲", "甲地", "restricted", [("count", "通行计数", "integer", False)], "2026-01-01")
        self.service.catalog.ingest("item-1", "flow", {"count": 8}, "2026-01-02")
        self.service.policies.set("甲地", "restricted", "运行协调", "allow", "2026-01-02", "机构甲")
        self.revision = self.service.products.assemble("report", "通行观察", ["item-1"], "2026-01-03", "机构甲")
        self.service.identity.register("ent-b", "机构乙", "2026-01-01")
        self.service.grants.issue("g-1", "flow", "机构乙", "运行协调", "2026-12-31", "2026-01-04")

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def delivery(self, delivery_id, recipient, at):
        return self.service.delivery.send(delivery_id, "report", self.revision, recipient, "运行协调", at)

    def test_existing_grant_still_works_after_rename(self):
        self.service.identity.rename("ent-b", "机构乙新名", "2026-02-01", "机构乙")
        with self.assertRaises(PermissionError):
            self.delivery("d-old", "机构乙", "2026-02-02")
        receipt = self.delivery("d-new", "机构乙新名", "2026-02-02")
        self.assertEqual(receipt["recipient_entity"], "ent-b")
        self.assertEqual(receipt["recipient"], "机构乙新名")
        trail = self.service.identity_trail("d-new")
        self.assertEqual(trail["grants"]["flow"][0]["basis"], "direct")

    def test_merger_does_not_inherit_grant_without_confirmation(self):
        self.service.identity.register("ent-c", "机构丙", "2026-01-01")
        self.service.identity.relate("r-1", "ent-b", "ent-c", "merger", "2026-03-01", "机构乙")
        with self.assertRaises(PermissionError) as caught:
            self.delivery("d-x", "机构丙", "2026-03-02")
        self.assertEqual(caught.exception.args[0][0]["reason"], "grant")

    def test_source_confirmation_extends_grant_to_successor(self):
        self.service.identity.register("ent-c", "机构丙", "2026-01-01")
        self.service.identity.relate("r-1", "ent-b", "ent-c", "merger", "2026-03-01", "机构乙")
        self.service.grants.authorize("a-1", "g-1", "ent-c", "merger", "运行协调", "continue", "2026-03-02", "机构甲")
        receipt = self.delivery("d-c", "机构丙", "2026-03-03")
        self.assertEqual(receipt["recipient_entity"], "ent-c")
        matches = json.loads(receipt["grant_paths"])["flow"]
        self.assertEqual(matches[0]["basis"], "succession")
        self.assertEqual(matches[0]["authorization"], "a-1")

    def test_withdrawing_merger_withdraws_successor_delivery(self):
        self.service.identity.register("ent-c", "机构丙", "2026-01-01")
        self.service.identity.relate("r-1", "ent-b", "ent-c", "merger", "2026-03-01", "机构乙")
        self.service.grants.authorize("a-1", "g-1", "ent-c", "merger", "运行协调", "continue", "2026-03-02", "机构甲")
        self.service.identity.withdraw("r-1", "2026-04-01", "机构乙")
        with self.assertRaises(PermissionError):
            self.delivery("d-c", "机构丙", "2026-04-02")

    def test_rename_cannot_be_confirmed_as_merger(self):
        self.service.identity.rename("ent-b", "机构乙新名", "2026-02-01", "机构乙")
        with self.assertRaises(IdentityError):
            self.service.grants.authorize("a-1", "g-1", "ent-b", "merger", "运行协调", "continue", "2026-02-02", "机构甲")
        with self.assertRaises(IdentityError):
            self.service.grants.authorize("a-2", "g-1", "ent-c", "rename", "运行协调", "continue", "2026-02-02", "机构甲")

    def test_source_actor_required_for_continuation(self):
        self.service.identity.register("ent-c", "机构丙", "2026-01-01")
        self.service.identity.relate("r-1", "ent-b", "ent-c", "merger", "2026-03-01", "机构乙")
        with self.assertRaises(PermissionError):
            self.service.grants.authorize("a-1", "g-1", "ent-c", "merger", "运行协调", "continue", "2026-03-02", "机构丙")

    def test_scope_cannot_be_expanded(self):
        self.service.identity.register("ent-c", "机构丙", "2026-01-01")
        self.service.identity.relate("r-1", "ent-b", "ent-c", "merger", "2026-03-01", "机构乙")
        with self.assertRaises(IdentityError):
            self.service.grants.authorize("a-1", "g-1", "ent-c", "merger", "其他用途", "continue", "2026-03-02", "机构甲")

    def test_ambiguous_name_rejected(self):
        self.service.identity.register("ent-d", "机构丁", "2026-01-01")
        # 机构乙仍在用该名称，另一个主体不能从同一时点起也使用它。
        with self.assertRaises(IdentityError):
            self.service.identity.rename("ent-d", "机构乙", "2026-02-01", "机构丁")

    def test_old_name_cannot_be_used_after_rename(self):
        self.service.identity.rename("ent-b", "机构乙新名", "2026-02-01", "机构乙")
        with self.assertRaises(PermissionError):
            self.delivery("d-old", "机构乙", "2026-02-02")

    def test_released_name_reused_after_rename(self):
        self.service.identity.rename("ent-b", "机构乙新名", "2026-02-01", "机构乙")
        self.service.identity.register("ent-d", "机构丁", "2026-01-01")
        self.service.identity.rename("ent-d", "机构乙", "2026-03-01", "机构丁")
        self.assertEqual(self.service.identity.entity("机构乙", "2026-01-15"), "ent-b")
        self.assertEqual(self.service.identity.entity("机构乙", "2026-03-02"), "ent-d")

    def test_old_receipt_keeps_original_recipient(self):
        receipt = self.delivery("d-old", "机构乙", "2026-02-01")
        self.service.identity.rename("ent-b", "机构乙新名", "2026-03-01", "机构乙")
        trail = self.service.identity_trail("d-old")
        self.assertEqual(trail["receipt"]["recipient"], "机构乙")
        self.assertEqual(trail["receipt"]["grant_paths"], json.loads(receipt["grant_paths"]))
        self.assertEqual(trail["recipient"]["display_name"], "机构乙新名")
        self.assertEqual(trail["recipient"]["names"], ["机构乙"])

    def test_cycle_rejected(self):
        self.service.identity.register("ent-c", "机构丙", "2026-01-01")
        self.service.identity.register("ent-d", "机构丁", "2026-01-01")
        self.service.identity.relate("r-1", "ent-b", "ent-c", "merger", "2026-03-01", "机构乙")
        with self.assertRaises(IdentityError):
            self.service.identity.relate("r-2", "ent-c", "ent-b", "merger", "2026-03-02", "机构丙")

    def test_split_successor_requires_its_own_confirmation(self):
        self.service.identity.register("ent-c", "机构丙", "2026-01-01")
        self.service.identity.register("ent-d", "机构丁", "2026-01-01")
        self.service.identity.relate("r-1", "ent-b", "ent-c", "split", "2026-03-01", "机构乙")
        self.service.identity.relate("r-2", "ent-b", "ent-d", "split", "2026-03-01", "机构乙")
        self.service.grants.authorize("a-1", "g-1", "ent-c", "split", "运行协调", "continue", "2026-03-02", "机构甲")
        self.delivery("d-c", "机构丙", "2026-03-03")
        with self.assertRaises(PermissionError):
            self.delivery("d-d", "机构丁", "2026-03-03")

    def test_authorization_withdrawn_before_delivery(self):
        self.service.identity.register("ent-c", "机构丙", "2026-01-01")
        self.service.identity.relate("r-1", "ent-b", "ent-c", "merger", "2026-03-01", "机构乙")
        self.service.grants.authorize("a-1", "g-1", "ent-c", "merger", "运行协调", "continue", "2026-03-02", "机构甲")
        self.service.grants.authorize("a-2", "g-1", "ent-c", "merger", "运行协调", "withdraw", "2026-03-03", "机构甲")
        with self.assertRaises(PermissionError):
            self.delivery("d-c", "机构丙", "2026-03-04")
        decisions = [row["decision"] for row in self.service.grants.authorizations("g-1")]
        self.assertEqual(decisions, ["continue", "withdraw"])

    def test_grant_without_registered_entity_can_be_linked_afterwards(self):
        self.service.grants.issue("g-2", "flow", "机构戊", "运行协调", "2026-12-31", "2026-01-05")
        self.assertEqual(self.service.grants.get("g-2")["recipient_entity"], None)
        self.service.identity.register("ent-e", "机构戊", "2026-01-01")
        self.service.grants.attribute("g-2", "ent-e", "2026-01-06", "机构甲")
        with self.assertRaises(PermissionError):
            self.service.grants.attribute("g-2", "ent-e", "2026-01-06", "机构戊")
        with self.assertRaises(IdentityError):
            self.service.grants.attribute("g-2", "ent-e", "2026-01-06", "机构甲")
        self.service.identity.register("ent-f", "机构己", "2026-01-01")
        self.service.identity.relate("r-1", "ent-e", "ent-f", "merger", "2026-03-01", "机构戊")
        self.service.grants.authorize("a-1", "g-2", "ent-f", "merger", "运行协调", "continue", "2026-03-02", "机构甲")
        receipt = self.delivery("d-f", "机构己", "2026-03-03")
        self.assertEqual(receipt["recipient_entity"], "ent-f")

    def test_attribute_cannot_move_grant_to_successor(self):
        self.service.grants.issue("g-2", "flow", "机构庚", "运行协调", "2026-12-31", "2026-01-05")
        self.service.identity.register("ent-g", "机构庚", "2026-01-01")
        self.service.identity.register("ent-h", "机构辛", "2026-01-01")
        self.service.identity.relate("r-1", "ent-g", "ent-h", "merger", "2026-02-01", "机构庚")
        with self.assertRaises(IdentityError):
            self.service.grants.attribute("g-2", "ent-h", "2026-02-02", "机构甲")

    def test_withdrawn_relation_cannot_be_confirmed_even_by_source(self):
        self.service.identity.register("ent-c", "机构丙", "2026-01-01")
        self.service.identity.relate("r-1", "ent-b", "ent-c", "merger", "2026-03-01", "机构乙")
        self.service.identity.withdraw("r-1", "2026-03-02", "机构乙")
        with self.assertRaises(IdentityError):
            self.service.grants.authorize("a-1", "g-1", "ent-c", "merger", "运行协调", "continue", "2026-03-03", "机构甲")

    def test_standing_reflects_withdrawn_relation(self):
        self.service.identity.rename("ent-b", "机构乙新名", "2026-02-01", "机构乙")
        receipt = self.delivery("d-new", "机构乙新名", "2026-02-02")
        self.assertEqual(
            self.service.identity_trail(receipt["id"], at="2026-02-03")["standing"][0]["grant_paths"][0]["basis"],
            "direct",
        )
        self.service.identity.register("ent-c", "机构丙", "2026-01-01")
        self.service.identity.relate("r-1", "ent-b", "ent-c", "merger", "2026-03-01", "机构乙")
        # 更名后的主体再次合并时，旧名称仍解析到合并前的主体；分支不会自动延伸到新主体。
        self.assertEqual(
            self.service.identity_trail(receipt["id"], at="2026-03-02")["standing"][0]["grant_paths"][0]["entity"],
            "ent-b",
        )
        self.service.identity.withdraw("r-1", "2026-04-01", "机构乙")
        trail = self.service.identity_trail(receipt["id"])
        self.assertEqual(trail["relations"][0]["status"], "withdrawn")
        self.assertEqual(trail["receipt"]["recipient"], "机构乙新名")


if __name__ == "__main__":
    unittest.main()
