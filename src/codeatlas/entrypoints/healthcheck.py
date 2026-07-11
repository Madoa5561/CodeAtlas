"""Process readiness healthcheck used by container orchestration."""

from codeatlas.infrastructure.health import is_process_ready


def main() -> None:
    """Exit unsuccessfully unless the started service process is alive."""
    if not is_process_ready():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
