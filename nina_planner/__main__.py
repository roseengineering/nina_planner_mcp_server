import logging

from .server import mcp

logger = logging.getLogger(__name__)


def main():
    logger.setLevel("INFO")
    mcp.run()


if __name__ == "__main__":
    main()
