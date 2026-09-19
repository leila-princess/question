"""一次性迁移脚本：规范 Indicator.Type 并回填 ParentId。"""
from __future__ import annotations

from flask import Flask

from config import Config


def run():
    app = Flask(__name__)
    app.config.from_object(Config)
    driver = app.config.get("NEO4J_DRIVER")
    if not driver:
        raise RuntimeError("Neo4j driver not available")
    with driver.session() as s:
        s.run(
            """
            MATCH (i:Indicator)
            OPTIONAL MATCH (p:Indicator)-[:HAS]->(i)
            WITH i, p,
                 CASE
                   WHEN toLower(coalesce(i.Type,'')) IN ['category', 'test'] THEN toLower(i.Type)
                   WHEN p IS NULL THEN 'category'
                   ELSE 'test'
                 END AS normalized_type
            SET i.Type = normalized_type,
                i.ParentId = CASE WHEN normalized_type = 'test' THEN coalesce(p.Id, '') ELSE '' END
            """
        ).consume()
    print("indicator hierarchy migration done")


if __name__ == "__main__":
    run()
