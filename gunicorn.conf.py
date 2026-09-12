def worker_exit(server, worker):
    # Runs inside the exiting worker, where the checker thread lives.
    from checker import stop_background_checker

    stop_background_checker()
