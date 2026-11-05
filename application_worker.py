"""Single durable worker running beside the web process on its shared volume."""
import logging
import signal
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from database import init_db
import applications as workflow

log = logging.getLogger('applications')


def run():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    init_db()
    workflow.recover_interrupted()
    while not stop.is_set():
        try:
            config = workflow.settings()
            now = datetime.now(ZoneInfo('Europe/London'))
            scheduled = (config.get('daily_discovery') == '1' and config.get('automation_enabled') == '1'
                         and now.hour >= 8 and config.get('last_discovery_day') != now.date().isoformat())
            requested = config.get('discovery_requested') == '1'
            if scheduled or requested:
                workflow.put_settings({'last_discovery_day': now.date().isoformat(), 'discovery_requested': '0'})
                result = workflow.discover()
                workflow.put_settings({'last_discovery_result': __import__('json').dumps(result)})
                log.info('Discovery completed: queued=%s errors=%s', result['queued'], len(result['errors']))
            worked = workflow.worker_once()
            stop.wait(2 if worked else 15)
        except Exception as exc:
            # Never log tokens, candidate information, URLs with secrets, or page bodies.
            log.warning('Application worker paused after %s', type(exc).__name__)
            stop.wait(30)


if __name__ == '__main__':
    run()
