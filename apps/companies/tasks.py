import logging

from celery import shared_task

from apps.companies.models import Source
from apps.companies.scraper import get_scraper

logger = logging.getLogger(__name__)


@shared_task
def scrape_source(source_id: int):
    try:
        source = Source.objects.get(pk=source_id)
    except Source.DoesNotExist:
        logger.error("scrape_source: source %d not found", source_id)
        return

    source.status = Source.STATUS_SCRAPING
    source.save(update_fields=["status"])

    scraper = get_scraper()

    try:
        result = scraper.scrape(source.url)
        source.scraped_data = result
        source.status = Source.STATUS_SCRAPED
        source.save(update_fields=["scraped_data", "status"])
    except Exception:
        logger.exception("Scrape failed for source %d (%s)", source_id, source.url)
        source.status = Source.STATUS_FAILED
        source.error_msg = "scrape failed"
        source.save(update_fields=["status", "error_msg"])
