from graphlib import TopologicalSorter

import pytest

from watch.catalog import CATALOGS, STACKS, AppName

APPS = sorted(CATALOGS)


def test_both_apps_have_a_catalog_named_after_their_compose_projects() -> None:
    assert {app: catalog.projects for app, catalog in CATALOGS.items()} == {
        "freight-audit": {"prod": "freightaudit-prod", "dev": "freightaudit"},
        "filing-lens": {"prod": "filinglens-prod", "dev": "filinglens"},
    }


@pytest.mark.parametrize("app", APPS)
def test_dependencies_name_services_of_the_same_app_and_never_loop(app: AppName) -> None:
    catalog = CATALOGS[app]
    names = [service.name for service in catalog.services]
    assert len(names) == len(set(names))
    graph = {service.name: set(service.depends_on) for service in catalog.services}
    assert all(dependency in names for needs in graph.values() for dependency in needs)
    tuple(TopologicalSorter(graph).static_order())  # raises CycleError on a loop


def test_containers_follow_compose_naming_and_run_only_where_they_exist() -> None:
    fa, fl = CATALOGS["freight-audit"], CATALOGS["filing-lens"]
    assert fa.container(fa.service("api"), "prod") == "freightaudit-prod-api-1"
    assert fa.container(fa.service("postgres"), "dev") == "freightaudit-postgres-1"
    # FreightAudit's dev api and worker run on the host, and its proxies only in production.
    assert fa.container(fa.service("api"), "dev") is None
    assert fa.container(fa.service("egress-proxy"), "dev") is None
    assert fl.container(fl.service("ingest"), "prod") == "filinglens-prod-ingest-1"
    assert fl.service("ingest").optional


def test_container_names_are_unique_across_both_apps_and_stacks() -> None:
    names = [
        name
        for catalog in CATALOGS.values()
        for service in catalog.services
        for stack in STACKS
        if (name := catalog.container(service, stack))
    ]
    assert len(names) == len(set(names))


@pytest.mark.parametrize("app", APPS)
def test_signals_read_kept_fields_and_rules_name_catalog_services(app: AppName) -> None:
    catalog = CATALOGS[app]
    assert catalog.level_field in catalog.log_fields
    assert all(signal.field in catalog.log_fields for signal in catalog.signals)
    alertnames = [signal.alertname for signal in catalog.signals]
    assert len(alertnames) == len(set(alertnames))
    names = {service.name for service in catalog.services}
    assert {rule.service for rule in catalog.rules.values()} <= names
    assert not set(catalog.rules) & set(alertnames)


def test_an_unknown_service_is_refused() -> None:
    with pytest.raises(KeyError, match="nginx"):
        CATALOGS["freight-audit"].service("nginx")
