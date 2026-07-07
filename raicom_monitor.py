#!/usr/bin/env python3
"""
睿抗机器人开发者大赛 (RAICOM) - 成绩公布监控工具
===============================================
实时监控 https://www.raicom.com.cn/ 的成绩公布页面，
当检测到包含"安徽"关键词的新公告时，通过多种渠道发送通知。

支持的通知方式:
- Server酱 (微信推送) - 推荐，最简单免费
- 邮件 (SMTP)
- 钉钉机器人
- 企业微信机器人

使用方法:
  1. 编辑 config.json 配置通知方式
  2. python3 raicom_monitor.py          # 持续运行模式
  3. python3 raicom_monitor.py --once   # 单次检查模式（适合cron定时任务）
"""

import json
import os
import sys
import time
import logging
import argparse
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
from urllib.parse import urlencode

# 尝试导入 requests，如果没有则提示安装
try:
    import requests
except ImportError:
    print("请先安装 requests 库: pip install requests --break-system-packages")
    sys.exit(1)

# ─── 配置 ───────────────────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")
STATE_PATH = os.path.join(SCRIPT_DIR, "monitor_state.json")
LOG_PATH = os.path.join(SCRIPT_DIR, "monitor.log")

# ─── 日志设置 ───────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("RAICOM-Monitor")


# ─── 工具函数 ───────────────────────────────────────────────────────────────

def load_config():
    """加载配置文件"""
    if not os.path.exists(CONFIG_PATH):
        logger.error(f"配置文件不存在: {CONFIG_PATH}")
        logger.error("请复制 config.json 并编辑配置后重试")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_state():
    """加载已处理过的文章ID记录"""
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"seen_ids": []}


def save_state(state):
    """保存已处理过的文章ID记录"""
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def timestamp_to_str(ts):
    """将毫秒时间戳转为可读字符串"""
    return datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M:%S")


# ─── API 请求 ───────────────────────────────────────────────────────────────

def fetch_article_list(config, page=1):
    """获取成绩公布列表"""
    params = {
        "docType": config["api"]["doc_type"],
        "page": page,
    }
    url = config["api"]["list_url"] + "?" + urlencode(params)
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") == 0:
            return data.get("data", [])
        else:
            logger.warning(f"API返回异常: {data}")
            return []
    except Exception as e:
        logger.error(f"请求列表API失败: {e}")
        return []


def fetch_article_detail(config, article_id):
    """获取文章详情"""
    try:
        resp = requests.get(
            config["api"]["detail_url"],
            params={"cid": article_id},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") == 0 and data.get("data"):
            return data["data"]
        return None
    except Exception as e:
        logger.error(f"请求文章详情失败 (id={article_id}): {e}")
        return None


# ─── 通知发送 ───────────────────────────────────────────────────────────────

def send_serverchan(config, title, content):
    """通过 Server酱 发送微信通知"""
    sc = config["notifications"]["serverchan"]
    # 优先从环境变量获取 SendKey（GitHub Actions 部署方式）
    send_key = os.environ.get("SERVERCHAN_SENDKEY") or sc.get("send_key", "")
    if not sc.get("enabled") or not send_key or send_key == "你的Server酱SendKey":
        logger.info("Server酱未配置，跳过")
        return False
    try:
        url = f"https://sctapi.ftqq.com/{send_key}.send"
        resp = requests.post(url, data={"title": title, "desp": content}, timeout=10)
        result = resp.json()
        if result.get("code") == 0:
            logger.info("Server酱通知发送成功")
            return True
        else:
            logger.warning(f"Server酱发送失败: {result}")
            return False
    except Exception as e:
        logger.error(f"Server酱发送异常: {e}")
        return False


def send_email(config, title, content):
    """通过邮件发送通知"""
    mail_cfg = config["notifications"]["email"]
    if not mail_cfg.get("enabled"):
        logger.info("邮件通知未配置，跳过")
        return False
    try:
        msg = MIMEText(content, "plain", "utf-8")
        msg["Subject"] = title
        msg["From"] = mail_cfg["sender"]
        msg["To"] = ", ".join(mail_cfg["receivers"])

        if mail_cfg.get("use_ssl", True):
            with smtplib.SMTP_SSL(mail_cfg["smtp_host"], mail_cfg["smtp_port"]) as server:
                server.login(mail_cfg["sender"], mail_cfg["password"])
                server.sendmail(mail_cfg["sender"], mail_cfg["receivers"], msg.as_string())
        else:
            with smtplib.SMTP(mail_cfg["smtp_host"], mail_cfg["smtp_port"]) as server:
                server.starttls()
                server.login(mail_cfg["sender"], mail_cfg["password"])
                server.sendmail(mail_cfg["sender"], mail_cfg["receivers"], msg.as_string())

        logger.info(f"邮件通知发送成功 -> {mail_cfg['receivers']}")
        return True
    except Exception as e:
        logger.error(f"邮件发送失败: {e}")
        return False


def send_dingtalk(config, title, content):
    """通过钉钉机器人发送通知"""
    dt = config["notifications"]["dingtalk"]
    if not dt.get("enabled"):
        logger.info("钉钉通知未配置，跳过")
        return False
    try:
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": f"## {title}\n\n{content}",
            },
        }
        resp = requests.post(dt["webhook_url"], json=payload, timeout=10)
        result = resp.json()
        if result.get("errcode") == 0:
            logger.info("钉钉通知发送成功")
            return True
        else:
            logger.warning(f"钉钉发送失败: {result}")
            return False
    except Exception as e:
        logger.error(f"钉钉发送异常: {e}")
        return False


def send_wecom(config, title, content):
    """通过企业微信机器人发送通知"""
    wc = config["notifications"]["wecom"]
    if not wc.get("enabled"):
        logger.info("企业微信通知未配置，跳过")
        return False
    try:
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": f"## {title}\n\n{content}",
            },
        }
        resp = requests.post(wc["webhook_url"], json=payload, timeout=10)
        result = resp.json()
        if result.get("errcode") == 0:
            logger.info("企业微信通知发送成功")
            return True
        else:
            logger.warning(f"企业微信发送失败: {result}")
            return False
    except Exception as e:
        logger.error(f"企业微信发送异常: {e}")
        return False


def send_notifications(config, article):
    """向所有已启用的渠道发送通知"""
    title = f"🎯 RAICOM 安徽成绩公布通知"
    detail = fetch_article_detail(config, article["id"])

    content_lines = [
        f"【标题】{article['docname']}",
        f"【发布时间】{timestamp_to_str(article['created'])}",
    ]
    if detail:
        if detail.get("showTime"):
            content_lines.append(f"【公示日期】{detail['showTime']}")
        if detail.get("downloadFile"):
            if detail.get("downloadUrl"):
                content_lines.append(f"【附件下载】{detail['downloadUrl']}")
            else:
                content_lines.append(f"【附件】{detail['downloadFile']}")
    content_lines.append(f"【查看详情】https://www.raicom.com.cn/content.html?cid={article['id']}")

    content = "\n".join(content_lines)

    logger.info(f"===== 发送通知 =====")
    logger.info(f"标题: {article['docname']}")

    channels = [
        ("Server酱", send_serverchan),
        ("邮件", send_email),
        ("钉钉", send_dingtalk),
        ("企业微信", send_wecom),
    ]

    sent_any = False
    for name, func in channels:
        try:
            if func(config, title, content):
                sent_any = True
        except Exception as e:
            logger.error(f"{name} 通知异常: {e}")

    # 控制台输出通知内容
    logger.info("=" * 50)
    logger.info(f"通知内容:\n{title}\n{content}")
    logger.info("=" * 50)

    return sent_any


# ─── 核心监控逻辑 ───────────────────────────────────────────────────────────

def check_for_updates(config):
    """检查是否有新的安徽成绩公布"""
    state = load_state()
    seen_ids = set(state.get("seen_ids", []))
    new_articles = []
    keywords = config["monitor"].get("keywords", ["安徽"])
    max_pages = config["monitor"].get("max_pages", 5)

    # 遍历多页获取最新文章
    for page in range(1, max_pages + 1):
        articles = fetch_article_list(config, page)
        if not articles:
            break

        for article in articles:
            aid = article["id"]
            title = article.get("docname", "")

            # 检查是否包含关键词
            matched = any(kw in title for kw in keywords)

            if matched and aid not in seen_ids:
                new_articles.append(article)
                seen_ids.add(aid)

        # 如果这一页的文章都在 seen_ids 中，说明已经处理过了，不再翻页
        if all(a["id"] in seen_ids for a in articles):
            break

    # 更新状态
    state["seen_ids"] = list(seen_ids)
    save_state(state)

    return new_articles


def run_once(config):
    """单次检查模式"""
    logger.info("开始单次检查...")
    new_articles = check_for_updates(config)

    if new_articles:
        logger.info(f"发现 {len(new_articles)} 条新的安徽成绩公布!")
        for article in new_articles:
            logger.info(f"  -> {article['docname']} ({timestamp_to_str(article['created'])})")
            send_notifications(config, article)
        return True
    else:
        logger.info("暂无新的安徽成绩公布")
        return False


def run_loop(config):
    """持续运行模式"""
    interval = config["api"].get("check_interval_seconds", 300)
    logger.info(f"启动持续监控模式，每 {interval} 秒检查一次")
    logger.info("按 Ctrl+C 停止运行")

    while True:
        try:
            run_once(config)
        except KeyboardInterrupt:
            logger.info("收到中断信号，停止监控")
            break
        except Exception as e:
            logger.error(f"运行异常: {e}")

        time.sleep(interval)


# ─── 命令行入口 ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="RAICOM 睿抗机器人开发者大赛 - 成绩公布监控工具"
    )
    parser.add_argument(
        "--once", action="store_true",
        help="单次检查模式（检查完即退出，适合配合cron定时任务使用）"
    )
    parser.add_argument(
        "--interval", type=int, default=None,
        help="检查间隔（秒），覆盖config.json中的设置"
    )
    parser.add_argument(
        "--keywords", type=str, default=None,
        help="监控关键词，多个用逗号分隔，如: 安徽,安徽省赛"
    )
    args = parser.parse_args()

    config = load_config()

    # 命令行参数覆盖
    if args.interval:
        config["api"]["check_interval_seconds"] = args.interval
    if args.keywords:
        config["monitor"]["keywords"] = [k.strip() for k in args.keywords.split(",")]

    # 显示配置信息
    logger.info("=" * 50)
    logger.info("RAICOM 成绩公布监控工具")
    logger.info(f"监控关键词: {config['monitor']['keywords']}")
    logger.info(f"检查间隔: {config['api']['check_interval_seconds']} 秒")
    logger.info("=" * 50)

    if args.once:
        run_once(config)
    else:
        run_loop(config)


if __name__ == "__main__":
    main()
