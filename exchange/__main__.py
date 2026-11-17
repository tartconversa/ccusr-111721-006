import argparse
import json

from .service import Exchange


def main():
    parser = argparse.ArgumentParser(description="本地协作台账演练")
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    service = Exchange(args.database)
    try:
        service.identity.register("ent-b", "机构乙", "2026-11-17")
        service.catalog.register("flow", "机构甲", "甲地", "business", [("count", "通行计数", "integer", False)], "2026-11-17")
        service.catalog.ingest("item-1", "flow", {"count": 7}, "2026-11-17")
        service.grants.issue("g-1", "flow", "机构乙", "协作统计", "2026-12-31", "2026-11-17")
        service.policies.set("甲地", "business", "协作统计", "allow", "2026-11-17", "机构甲")
        revision = service.products.assemble("report", "通行汇总", ["item-1"], "2026-11-17", "机构甲")
        receipt = service.delivery.send("d-1", "report", revision, "机构乙", "协作统计", "2026-11-17")
        service.identity.rename("ent-b", "机构乙新名", "2026-11-18", "机构乙")
        service.identity.register("ent-c", "机构丙", "2026-11-18")
        service.identity.relate("rel-1", "ent-b", "ent-c", "merger", "2026-11-18", "机构乙")
        service.grants.authorize("auth-1", "g-1", "ent-c", "merger", "协作统计", "continue", "2026-11-18", "机构甲")
        renewed = service.delivery.send("d-2", "report", revision, "机构丙", "协作统计", "2026-11-18")
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
        print(json.dumps(renewed, ensure_ascii=False, sort_keys=True))
        print(json.dumps(service.identity_trail("d-2", at="2026-11-18"), ensure_ascii=False, sort_keys=True))
    finally:
        service.close()


if __name__ == "__main__":
    main()
