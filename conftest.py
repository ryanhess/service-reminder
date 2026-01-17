from pytest import fixture
from pytest_mock import MockerFixture
from fastapi import Response


class fastApiTemplateResponseSpyClass:
    """
    Captures calls to TemplateResponse for inspection in tests.

    Usage:
        def test_something(client, template_spy):
            client.post('/some/route', data={...})
            result = template_spy.collect()
            assert result['data'].get('errorMessage') == 'expected'
    """

    def __init__(self):
        self._template = None
        self._data = {}

    def __call__(self, request=None, templateFile="", templateData=None, **kwargs):
        """Called when TemplateResponse is invoked."""
        self._template = templateFile
        self._data = templateData or {}
        return Response(status_code=200)

    def collect(self):
        """Return captured data and reset state for next assertion."""
        result = {
            'template': self._template,
            'data': self._data
        }
        self._template = None
        self._data = {}
        return result


@fixture
def fastApiTemplateResponseParamsSpy(mocker: MockerFixture):
    """
    Fixture that mocks TemplateResponse and captures its calls.

    Returns a TemplateSpy instance that can be used to inspect
    what data was passed to the template.
    """
    spyInstance = fastApiTemplateResponseSpyClass()
    mocker.patch('main.templates.TemplateResponse', spyInstance)
    return spyInstance
