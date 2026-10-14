import json

import boto3
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError
from types_boto3_sqs import SQSClient

from agent.main import create_app
from agent.settings import Settings
from py_config import load_settings

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def settings() -> Settings:
    return load_settings(Settings)


def test_queue_is_fifo_and_redrives_to_dlq_after_five_receives(settings: Settings) -> None:
    sqs: SQSClient = boto3.client(
        "sqs", endpoint_url=settings.sqs_endpoint_url, region_name=settings.aws_region
    )
    url = sqs.get_queue_url(QueueName=settings.investigations_queue)["QueueUrl"]
    attributes = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["All"])["Attributes"]
    policy = json.loads(attributes["RedrivePolicy"])

    assert attributes["FifoQueue"] == "true"
    assert int(policy["maxReceiveCount"]) == 5
    assert policy["deadLetterTargetArn"].endswith(":investigations-dlq.fifo")


def test_ready_against_real_infra(settings: Settings) -> None:
    assert TestClient(create_app(settings)).get("/readyz").status_code == 200


def test_agent_role_cannot_write_into_gateway_schema(settings: Settings) -> None:
    engine = create_engine(settings.agent_database_url)
    with engine.connect() as connection, pytest.raises(ProgrammingError, match="permission denied"):
        connection.execute(text("create table gateway.not_ours (id int)"))
    engine.dispose()
