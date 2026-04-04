import os
from codegraphcontext.core import get_database_manager

os.environ["DATABASE_TYPE"] = "kuzudb"
db_manager = get_database_manager()
driver = db_manager.get_driver()

with driver.session() as session:
    res = session.run("MATCH (n:Function) RETURN n.name as name LIMIT 10")
    print("Functions in CGC:")
    for r in res.data():
        print(f" - {r['name']}")
    
    res = session.run("MATCH (n:File) RETURN n.path as path LIMIT 10")
    print("\nFiles in CGC:")
    for r in res.data():
        print(f" - {r['path']}")
