import json
from typing import TYPE_CHECKING

import boto3
import pytest
from conftest import REFUSED_DATABASE
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.exc import OperationalError, ProgrammingError
from types_boto3_sqs import SQSClient

from agent.main import create_app
from agent.settings import Settings
from py_config import load_settings

if TYPE_CHECKING:
    from conftest import ScratchDb

pytestmark = pytest.mark.integration


def test_queue_is_fifo_and_redrives_to_dlq_after_five_receives() -> None:
    settings = load_settings(Settings)
    sqs: SQSClient = boto3.client(
        "sqs", endpoint_url=settings.sqs_endpoint_url, region_name=settings.aws_region
    )
    url = sqs.get_queue_url(QueueName=settings.investigations_queue)["QueueUrl"]
    attributes = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["All"])["Attributes"]
    policy = json.loads(attributes["RedrivePolicy"])

    assert attributes["FifoQueue"] == "true"
    assert int(policy["maxReceiveCount"]) == 5
    assert policy["deadLetterTargetArn"].endswith(":investigations-dlq.fifo")


def test_ready_against_real_infra(scratch_db: "ScratchDb") -> None:
    settings = load_settings(Settings)
    assert TestClient(create_app(settings)).get("/readyz").status_code == 200


def test_agent_role_cannot_write_into_gateway_schema(scratch_db: "ScratchDb") -> None:
    engine = create_engine(scratch_db.agent_url)
    with engine.connect() as connection, pytest.raises(ProgrammingError, match="permission denied"):
        connection.execute(text("create table gateway.not_ours (id int)"))
    engine.dispose()


def test_tests_never_reach_the_dev_database() -> None:
    # Without a scratch_db fixture, the URL names a database that does not exist.
    url = load_settings(Settings).agent_database_url
    assert make_url(url).database == REFUSED_DATABASE
    engine = create_engine(url)
    with pytest.raises(OperationalError, match="does not exist"), engine.connect():
        pass
    engine.dispose()
