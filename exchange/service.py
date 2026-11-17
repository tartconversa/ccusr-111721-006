import json

from .audit import history
from .catalog import Catalog
from .db import connect
from .delivery import Delivery
from .grants import Grants
from .identity import Identity
from .policy import Policies
from .products import Products


class Exchange:
    def __init__(self, database):
        self.connection = connect(database)
        self.identity = Identity(self.connection)
        self.catalog = Catalog(self.connection)
        self.grants = Grants(self.connection)
        self.policies = Policies(self.connection)
        self.products = Products(self.connection)
        self.delivery = Delivery(self.connection)

    def history(self, subject):
        return history(self.connection, subject)

    def identity_trail(self, delivery_id, at=None):
        """交付凭据当时指向的接收方，与之后登记的主体身份变动。

        返回 {receipt, recipient, grants, relations, events}。receipt 保持原样，
        不会因后来的更名或合并而被改写；relations 列出与该交付相关的主体发生过的
        身份变动及其状态（active/withdrawn）。给出 at 时，standing 按该时点重新
        判断每一来源的许可路径，空列表表示当前领取已不被允许。
        """
        receipt = self.connection.execute(
            "SELECT * FROM deliveries WHERE id=?", (delivery_id,)
        ).fetchone()
        if receipt is None:
            raise KeyError(delivery_id)
        receipt = dict(receipt)
        receipt["grant_paths"] = json.loads(receipt["grant_paths"])
        entity = receipt["recipient_entity"]
        issued_at = receipt["created_at"]
        related = set()
        grants = set()
        for matches in receipt["grant_paths"].values():
            for match in matches:
                grants.add(match["grant"])
                for step in match.get("path", []):
                    related.add(step["predecessor"])
                    related.add(step["successor"])
        if entity:
            related.add(entity)
        relations = {}
        for entity_id in sorted(related):
            for relation in self.identity.relations_for(entity_id, "9999-12-31"):
                relations[relation["id"]] = relation
        subjects = {receipt["product_id"], delivery_id} | grants | related | set(relations)
        trail = {
            "receipt": receipt,
            "recipient": self.identity.describe(entity, issued_at) if entity else None,
            "grants": receipt["grant_paths"],
            "relations": sorted(relations.values(), key=lambda row: (row["at"], row["id"])),
            "events": [event for subject in sorted(subjects) for event in self.history(subject)],
        }
        if at is not None:
            trail["standing"] = [
                {
                    "source": source,
                    "grant_paths": self.grants.resolve(
                        source, receipt["recipient"], receipt["purpose"], at, entity
                    ),
                }
                for source in sorted(receipt["grant_paths"])
            ]
        return trail

    def close(self):
        self.connection.close()
