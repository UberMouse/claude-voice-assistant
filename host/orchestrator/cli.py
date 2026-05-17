import asyncio, logging
from .runner import amain

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(amain())


if __name__ == "__main__":
    main()
