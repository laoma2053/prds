"""Worker 入口 - 启动所有异步 Worker"""

import asyncio
import logging

from app.workers.delete_worker import run_delete_worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


async def main():
    """启动所有 Worker"""
    logger.info("🚀 PRDS Worker 启动")
    await asyncio.gather(
        run_delete_worker(interval=30),
        # 后续可扩展更多 worker:
        # run_cleanup_worker(),
        # run_health_check_worker(),
    )


if __name__ == "__main__":
    asyncio.run(main())
