"""Run the real worker outside HTTP for legacy result-equivalence assertions."""

from ramp_optimizer_api.local_worker import LocalWorker


def completed_result(client, response):
    assert response.status_code == 202, response.text
    service = client.app.state.job_service
    while service.get(response.json()["id"]).status == "QUEUED":
        assert LocalWorker(service).run_once()
    result = client.get(f"/api/v1/optimization-jobs/{response.json()['id']}/result")
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "SUCCEEDED", result.text
    return result.json()["result"]
