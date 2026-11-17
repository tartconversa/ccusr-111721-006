import json

from .audit import write
from .errors import IdentityError
from .identity import RELATION_KINDS, Identity

CONTINUATIONS = ("continue", "withdraw")


class Grants:
    def __init__(self, connection):
        self.connection = connection
        self.identity = Identity(connection)

    def issue(self, grant_id, source_id, recipient, purpose, valid_until, at, entity=None):
        """签发许可。

        recipient 是签发当时使用的接收方名称；entity 是该名称对应的法律主体。
        未显式给出时按名称在签发时点解析，无法解析的名称仍可作为历史名称签发，
        但没有主体身份，日后不能自动延续。
        """
        source = self.connection.execute("SELECT owner FROM sources WHERE id=?", (source_id,)).fetchone()
        if source is None:
            raise KeyError(source_id)
        if entity is None:
            entity = self.identity.entity(recipient, at)
        elif self.identity.entity(recipient, at) not in (None, entity):
            raise IdentityError("名称与主体在签发时点不一致")
        with self.connection:
            self.connection.execute(
                "INSERT INTO grants(id,source_id,recipient,purpose,valid_until,recipient_entity,issued_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (grant_id, source_id, recipient, purpose, valid_until, entity, at),
            )
            write(
                self.connection, at, source["owner"], "grant.issue", grant_id,
                {"source": source_id, "recipient": recipient, "entity": entity},
            )
        return grant_id

    def get(self, grant_id):
        row = self.connection.execute("SELECT * FROM grants WHERE id=?", (grant_id,)).fetchone()
        if row is None:
            raise KeyError(grant_id)
        return dict(row)

    def issued_at(self, grant):
        """许可的签发时点；旧台账未存该列时回退到审计记录。"""
        if grant["issued_at"]:
            return grant["issued_at"]
        row = self.connection.execute(
            "SELECT MIN(at) FROM audit WHERE action='grant.issue' AND subject=?",
            (grant["id"],),
        ).fetchone()
        return row[0] if row and row[0] else None

    def permits(self, source_id, recipient, purpose, at):
        """该时点是否存在允许领取的许可；返回首个许可编号或 None。

        与 resolve 使用同一套主体身份判断，不会因名称相同而绕过更名后的失效或
        未获来源方确认的合并。
        """
        matches = self.resolve(source_id, recipient, purpose, at)
        return matches[0]["grant"] if matches else None

    def attribute(self, grant_id, entity_id, at, actor):
        """把签发时尚未登记主体的许可登记到该名称当时所属的主体。

        只补记身份，不改许可范围、用途或接收方名称。要求名称在签发时点确实解析到
        该主体，因此不能用它把旧许可“改挂”到合并后的新主体；那种情况必须由来源方
        通过 authorize 确认延续。
        """
        grant = self.get(grant_id)
        owner = self.connection.execute(
            "SELECT owner FROM sources WHERE id=?", (grant["source_id"],)
        ).fetchone()["owner"]
        if actor != owner:
            raise PermissionError("只有来源方可以补记许可主体")
        if grant["recipient_entity"] is not None:
            raise IdentityError("许可已登记主体")
        issued_at = self.issued_at(grant)
        if issued_at is None:
            raise IdentityError("许可缺少签发时间，无法核对名称")
        # 名称可能在签发后转给其他主体；补记必须对照签发时点的名称归属，
        # 否则就是把许可挪给了一个当时并不存在的接收方。
        resolved = self.identity.entity(grant["recipient"], issued_at)
        if resolved != entity_id:
            raise IdentityError("名称在签发时点并不属于该主体")
        if not self.identity.names(entity_id, at):
            raise IdentityError("主体在补记时点没有有效名称")
        with self.connection:
            self.connection.execute(
                "UPDATE grants SET recipient_entity=? WHERE id=? AND recipient_entity IS NULL",
                (entity_id, grant_id),
            )
            write(
                self.connection, at, actor, "grant.attribute", grant_id,
                {
                    "entity": entity_id,
                    "recipient": grant["recipient"],
                    "issued_at": issued_at,
                },
            )
        return grant_id

    def resolve(self, source_id, recipient, purpose, at, entity=None):
        """列出在该时点允许接收方领取某来源的许可路径。

        返回若干 {grant, basis, ...}。basis 为 direct 表示许可本来就签给该法律
        主体（名称变化不影响）；basis 为 succession 表示经来源方确认、沿仍有效
        的主体关系延续。两者都没有时返回空列表。
        """
        if entity is None:
            entity = self.identity.entity(recipient, at)
        matches = []
        direct = self.connection.execute(
            "SELECT * FROM grants WHERE source_id=? AND purpose=? AND status='active' "
            "AND valid_until>=? "
            "AND (recipient_entity=? OR (recipient_entity IS NULL AND ? IS NULL AND recipient=?)) "
            "ORDER BY id",
            (source_id, purpose, at, entity, entity, recipient),
        ).fetchall()
        for grant in direct:
            matches.append(
                {
                    "grant": grant["id"],
                    "basis": "direct",
                    "recipient": grant["recipient"],
                    "entity": grant["recipient_entity"] or entity,
                }
            )
        if entity is None:
            return matches
        successions = self.connection.execute(
            "SELECT g.*,s.old_recipient,s.transition,s.authorization_id,a.scope,a.path "
            "FROM grant_successions s "
            "JOIN grants g ON g.id=s.grant_id "
            "JOIN authorizations a ON a.id=s.authorization_id "
            "WHERE s.new_entity=? AND g.source_id=? AND g.purpose=? AND a.scope=? "
            "AND a.decision='continue' AND g.status='active' AND g.valid_until>=? "
            "ORDER BY g.id",
            (entity, source_id, purpose, purpose, at),
        ).fetchall()
        for row in successions:
            predecessor = row["recipient_entity"]
            if predecessor is None:
                issued_at = self.issued_at(row)
                if issued_at is None:
                    continue
                predecessor = self.identity.entity(row["recipient"], issued_at)
            if predecessor is None:
                continue
            path = self.identity.path(predecessor, entity, at)
            if path is None:
                continue
            # 授权确认绑定的是确认当时的身份关系路径；之后撤回关系或改变路径，
            # 原确认不再成立，必须重新由来源方确认。
            confirmed = json.loads(row["path"])
            if [step["id"] for step in path] != [step["relation"] for step in confirmed]:
                continue
            matches.append(
                {
                    "grant": row["id"],
                    "basis": "succession",
                    "recipient": row["old_recipient"],
                    "entity": entity,
                    "transition": row["transition"],
                    "authorization": row["authorization_id"],
                    "path": [
                        {
                            "relation": step["id"],
                            "kind": step["kind"],
                            "predecessor": step["predecessor_id"],
                            "successor": step["successor_id"],
                        }
                        for step in path
                    ],
                }
            )
        return matches

    def authorize(self, authorization_id, grant_id, entity_id, transition, purpose, decision, at, actor):
        """来源方就主体变动后原许可是否延续作出确认。

        purpose 必须等于原许可用途（不能借续展扩大范围），且 entity_id 必须在该
        时点通过仍有效的主体关系承接原接收方。withdraw 会撤销此前对同一主体作出的
        延续，但不改写已发出的交付凭据。更名不是关系型身份变动：主体不变，原许可
        本来就由该主体持有，无需也不允许用延续确认来“转移”。
        """
        if transition not in RELATION_KINDS:
            raise IdentityError("身份变动类型无效；更名不改变法律主体，无需确认延续")
        if decision not in CONTINUATIONS:
            raise IdentityError("延续决定无效")
        grant = self.get(grant_id)
        if grant["status"] != "active":
            raise IdentityError("许可已失效")
        if at > grant["valid_until"]:
            raise IdentityError("许可已过期")
        if purpose != grant["purpose"]:
            raise IdentityError("延续范围不能超出原许可用途")
        owner = self.connection.execute(
            "SELECT owner FROM sources WHERE id=?", (grant["source_id"],)
        ).fetchone()["owner"]
        if actor != owner:
            raise PermissionError("只有来源方可以确认许可延续")
        predecessor = grant["recipient_entity"]
        if predecessor is None:
            issued_at = self.issued_at(grant)
            predecessor = self.identity.entity(grant["recipient"], issued_at) if issued_at else None
        if predecessor is None:
            raise IdentityError("原接收方尚未登记为法律主体，无法确认延续")
        path = self.identity.path(predecessor, entity_id, at)
        if path is None:
            raise IdentityError("主体关系未登记或已撤回")
        if not any(step["kind"] == transition for step in path):
            raise IdentityError("身份变动类型与登记的关系不一致")
        trail = [
            {"relation": step["id"], "kind": step["kind"], "successor": step["successor_id"]}
            for step in path
        ]
        with self.connection:
            self.connection.execute(
                "INSERT INTO authorizations(id,source_id,grant_id,transition,scope,decision,path,decided_by,at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    authorization_id, grant["source_id"], grant_id, transition, purpose, decision,
                    json.dumps(trail, ensure_ascii=False, sort_keys=True), actor, at,
                ),
            )
            if decision == "continue":
                self.connection.execute(
                    "INSERT INTO grant_successions(grant_id,old_recipient,new_entity,transition,authorization_id,at,actor) "
                    "VALUES(?,?,?,?,?,?,?) "
                    "ON CONFLICT(grant_id,new_entity) DO UPDATE SET old_recipient=excluded.old_recipient,"
                    "transition=excluded.transition,authorization_id=excluded.authorization_id,"
                    "at=excluded.at,actor=excluded.actor",
                    (grant_id, grant["recipient"], entity_id, transition, authorization_id, at, actor),
                )
            else:
                self.connection.execute(
                    "DELETE FROM grant_successions WHERE grant_id=? AND new_entity=?",
                    (grant_id, entity_id),
                )
            write(
                self.connection, at, actor, "grant.authorize", grant_id,
                {
                    "authorization": authorization_id,
                    "predecessor": predecessor,
                    "entity": entity_id,
                    "transition": transition,
                    "scope": purpose,
                    "decision": decision,
                    "path": trail,
                },
            )
        return authorization_id

    def successions(self, grant_id):
        rows = self.connection.execute(
            "SELECT * FROM grant_successions WHERE grant_id=? ORDER BY new_entity", (grant_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def authorizations(self, grant_id):
        rows = self.connection.execute(
            "SELECT * FROM authorizations WHERE grant_id=? ORDER BY at,id", (grant_id,)
        ).fetchall()
        return [dict(row) for row in rows]
