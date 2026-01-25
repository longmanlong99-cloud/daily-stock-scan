import os
from notion_client import Client
from datetime import datetime

# 1. 获取环境变量（就是刚才保存的秘密）
notion_token = os.environ.get("NOTION_TOKEN")
database_id = os.environ.get("NOTION_DATABASE_ID")

# 2. 连接 Notion
notion = Client(auth=notion_token)

# 3. 准备要发送的数据
stock_name = "测试股票-RDW"
price = "12.5"
today = datetime.now().strftime("%Y-%m-%d")

print(f"开始尝试写入 Notion: {stock_name}...")

# 4. 写入操作
notion.pages.create(
    parent={"database_id": database_id},
    properties={
        "Name": {"title": [{"text": {"content": stock_name}}]}, # 标题列必须叫 Name
        "Tags": {"multi_select": [{"name": "观察池"}]},        # 标签列必须叫 Tags
    }
)

print("✅ 成功！快去 Notion 看看！")