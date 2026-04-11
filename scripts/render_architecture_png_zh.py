from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "langgraph-architecture-interview-zh.png"

WIDTH, HEIGHT = 2600, 1800


def load_font(path: str, size: int):
    return ImageFont.truetype(path, size)


FONT_TITLE = load_font(r"C:\Windows\Fonts\msyhbd.ttc", 42)
FONT_SECTION = load_font(r"C:\Windows\Fonts\msyhbd.ttc", 24)
FONT_BOX_TITLE = load_font(r"C:\Windows\Fonts\msyhbd.ttc", 22)
FONT_BOX_TEXT = load_font(r"C:\Windows\Fonts\msyh.ttc", 18)
FONT_SMALL = load_font(r"C:\Windows\Fonts\msyh.ttc", 16)


COLORS = {
    "client": ("#FFF4D6", "#D7A735"),
    "gateway": ("#DFF1FF", "#3E8ED0"),
    "orch": ("#E8E0FF", "#7856D8"),
    "gosup": ("#E5F7EC", "#2F9E61"),
    "ing": ("#FFE6DC", "#D96C3F"),
    "storage": ("#F1F3F5", "#6C757D"),
    "arrow": "#495057",
}


def draw_section(draw: ImageDraw.ImageDraw, x1, y1, x2, y2, title, fill, outline):
    draw.rounded_rectangle((x1, y1, x2, y2), radius=20, fill=fill, outline=outline, width=3)
    draw.rounded_rectangle((x1 + 14, y1 - 18, x1 + 250, y1 + 24), radius=14, fill=outline, outline=outline)
    draw.text((x1 + 28, y1 - 10), title, font=FONT_SECTION, fill="white")


def draw_box(draw: ImageDraw.ImageDraw, box, title, lines, outline):
    x1, y1, x2, y2 = box
    draw.rounded_rectangle((x1, y1, x2, y2), radius=18, fill="white", outline=outline, width=3)
    draw.text((x1 + 18, y1 + 14), title, font=FONT_BOX_TITLE, fill="#212529")
    draw.multiline_text((x1 + 18, y1 + 54), "\n".join(lines), font=FONT_BOX_TEXT, fill="#343A40", spacing=6)


def center(box):
    x1, y1, x2, y2 = box
    return ((x1 + x2) // 2, (y1 + y2) // 2)


def left_mid(box):
    x1, y1, x2, y2 = box
    return (x1, (y1 + y2) // 2)


def right_mid(box):
    x1, y1, x2, y2 = box
    return (x2, (y1 + y2) // 2)


def top_mid(box):
    x1, y1, x2, y2 = box
    return ((x1 + x2) // 2, y1)


def bottom_mid(box):
    x1, y1, x2, y2 = box
    return ((x1 + x2) // 2, y2)


def arrow(draw: ImageDraw.ImageDraw, p1, p2, width=4, color="#495057"):
    import math

    draw.line([p1, p2], fill=color, width=width)
    angle = math.atan2(p2[1] - p1[1], p2[0] - p1[0])
    size = 12
    left = (p2[0] - size * math.cos(angle - math.pi / 6), p2[1] - size * math.sin(angle - math.pi / 6))
    right = (p2[0] - size * math.cos(angle + math.pi / 6), p2[1] - size * math.sin(angle + math.pi / 6))
    draw.polygon([p2, left, right], fill=color)


def main():
    image = Image.new("RGB", (WIDTH, HEIGHT), "white")
    draw = ImageDraw.Draw(image)

    draw.text((60, 28), "PaiSmart LangGraph / LangChain 中文架构图", font=FONT_TITLE, fill="#111827")
    draw.text((62, 86), "适合面试讲解的职责分层版本", font=FONT_SMALL, fill="#6B7280")

    draw_section(draw, 40, 140, 420, 320, "客户端层", *COLORS["client"])
    draw_section(draw, 40, 360, 620, 790, "Go 接入层", *COLORS["gateway"])
    draw_section(draw, 690, 140, 2530, 920, "Python AI 编排层", *COLORS["orch"])
    draw_section(draw, 690, 950, 1680, 1320, "Go 内部能力层", *COLORS["gosup"])
    draw_section(draw, 40, 830, 620, 1320, "离线文档处理层", *COLORS["ing"])
    draw_section(draw, 40, 1360, 2530, 1735, "数据与基础设施层", *COLORS["storage"])

    user_box = (110, 195, 350, 270)
    ws_box = (90, 430, 300, 620)
    api_box = (340, 430, 550, 620)

    lg_box = (760, 220, 1030, 410)
    plan_box = (1080, 220, 1350, 410)
    ret_box = (1400, 220, 1670, 410)
    fuse_box = (1720, 220, 1990, 410)
    prompt_box = (2040, 220, 2310, 410)
    gen_box = (1110, 520, 1380, 680)

    orchapi_box = (780, 1020, 1080, 1230)
    search_box = (1140, 1020, 1440, 1230)
    chatstore_box = (1500, 1020, 1640, 1230)

    kafka_box = (80, 920, 260, 1120)
    proc_box = (310, 920, 490, 1120)
    ing_box = (80, 1150, 490, 1285)

    mysql_box = (90, 1450, 430, 1645)
    redis_box = (470, 1450, 810, 1645)
    minio_box = (850, 1450, 1190, 1645)
    es_box = (1230, 1450, 1570, 1645)
    tika_box = (1610, 1450, 1950, 1645)
    model_box = (1990, 1450, 2330, 1645)

    draw_box(draw, user_box, "用户与前端", ["Web 页面", "上传 文档检索 聊天"], "#D7A735")
    draw_box(draw, ws_box, "Go 网关", ["鉴权", "会话接入", "流式转发", "停止控制"], "#3E8ED0")
    draw_box(draw, api_box, "业务接口", ["上传", "文档管理", "后台管理", "用户与权限"], "#3E8ED0")

    draw_box(draw, lg_box, "LangGraph 状态图", ["查询规划", "状态流转", "多轮编排", "生成链路控制"], "#7856D8")
    draw_box(draw, plan_box, "规划节点", ["意图识别", "查询改写", "追问判断", "检索模式选择"], "#7856D8")
    draw_box(draw, ret_box, "检索器层", ["BM25 检索器", "向量检索器", "混合检索器", "记忆检索器"], "#7856D8")
    draw_box(draw, fuse_box, "融合与精排", ["RRF 融合", "上下文去重", "Cross Encoder 精排"], "#7856D8")
    draw_box(draw, prompt_box, "提示词构建", ["系统规则", "历史窗口", "记忆前导", "上下文组装"], "#7856D8")
    draw_box(draw, gen_box, "生成节点", ["流式生成", "Token 输出"], "#7856D8")

    draw_box(draw, orchapi_box, "内部编排接口", ["会话读取", "提示上下文", "知识检索", "记忆检索", "重排与持久化"], "#2F9E61")
    draw_box(draw, search_box, "搜索服务", ["BM25", "向量检索", "权限过滤", "检索降级"], "#2F9E61")
    draw_box(draw, chatstore_box, "会话与记忆服务", ["聊天历史", "工作记忆", "长期记忆写回"], "#2F9E61")

    draw_box(draw, kafka_box, "Kafka 阶段流水线", ["parse", "chunk", "embed", "index", "重试与重放"], "#D96C3F")
    draw_box(draw, proc_box, "Go 处理器", ["阶段调度", "回退执行", "存储落地"], "#D96C3F")
    draw_box(draw, ing_box, "Python Ingestion Worker", ["Tika 解析", "文本切分", "向量化", "批量索引"], "#D96C3F")

    draw_box(draw, mysql_box, "MySQL", ["用户数据", "文档元数据", "分块记录", "流水线任务"], "#6C757D")
    draw_box(draw, redis_box, "Redis", ["上传进度", "会话历史", "向量缓存"], "#6C757D")
    draw_box(draw, minio_box, "MinIO", ["分片文件", "合并文件", "解析文本中间产物"], "#6C757D")
    draw_box(draw, es_box, "Elasticsearch", ["知识索引", "记忆索引", "检索与召回"], "#6C757D")
    draw_box(draw, tika_box, "Tika", ["多格式文档抽取"], "#6C757D")
    draw_box(draw, model_box, "模型服务", ["LLM", "Embedding", "Reranker"], "#6C757D")

    arrow(draw, bottom_mid(user_box), top_mid(ws_box))
    arrow(draw, (center(user_box)[0] + 40, bottom_mid(user_box)[1]), top_mid(api_box))

    arrow(draw, right_mid(ws_box), left_mid(lg_box))
    arrow(draw, right_mid(lg_box), left_mid(plan_box))
    arrow(draw, right_mid(plan_box), left_mid(ret_box))
    arrow(draw, right_mid(ret_box), left_mid(fuse_box))
    arrow(draw, right_mid(fuse_box), left_mid(prompt_box))
    arrow(draw, bottom_mid(prompt_box), top_mid(gen_box))
    arrow(draw, left_mid(gen_box), (ws_box[2], center(ws_box)[1] + 20))

    arrow(draw, bottom_mid(ret_box), top_mid(orchapi_box))
    arrow(draw, right_mid(orchapi_box), left_mid(search_box))
    arrow(draw, (orchapi_box[2] - 20, orchapi_box[3]), (chatstore_box[0] + 30, chatstore_box[1]))
    arrow(draw, bottom_mid(search_box), top_mid(es_box))
    arrow(draw, bottom_mid(chatstore_box), top_mid(redis_box))
    arrow(draw, (chatstore_box[0] + 50, chatstore_box[3]), (mysql_box[2] - 40, mysql_box[1]))

    arrow(draw, bottom_mid(api_box), top_mid(kafka_box))
    arrow(draw, right_mid(kafka_box), left_mid(proc_box))
    arrow(draw, bottom_mid(proc_box), (center(ing_box)[0] + 80, ing_box[1]))
    arrow(draw, bottom_mid(proc_box), top_mid(minio_box))
    arrow(draw, (proc_box[2] - 30, proc_box[3]), top_mid(redis_box))
    arrow(draw, (proc_box[2], proc_box[3] - 10), top_mid(mysql_box))
    arrow(draw, right_mid(ing_box), left_mid(tika_box))
    arrow(draw, (ing_box[2] - 40, ing_box[3]), top_mid(model_box))
    arrow(draw, (ing_box[2] - 120, ing_box[3]), top_mid(es_box))

    draw.text((70, 1745), "说明：Go 负责工程底座与数据面，Python 负责 AI 编排与可插拔执行面", font=FONT_SMALL, fill="#4B5563")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUT)
    print(OUT)


if __name__ == "__main__":
    main()
