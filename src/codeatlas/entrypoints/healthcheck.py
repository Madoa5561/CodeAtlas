"""Local configuration healthcheck used by container orchestration."""

from codeatlas.bootstrap import create_application


def main() -> None:
    """Validate that the M1 application container can be composed."""
    create_application()


if __name__ == "__main__":
    main()
