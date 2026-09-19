import chromadb

client = chromadb.Client()  # 或 chromadb.PersistentClient(path="./chroma_data")

# 列出所有集合
collections = client.list_collections()
for name in collections:
    client.delete_collection(name)