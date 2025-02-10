import logging
from datetime import datetime, timezone

from flask import Flask, render_template
from flask_s3 import FlaskS3
from flask.logging import default_handler
from werkzeug.exceptions import HTTPException

from arxiv.base import Base
from arxiv.db import config_query_timing, configure_db
from arxiv.integration.fastly.headers import add_surrogate_key

from oaipmh.data.oai_errors import OAIException
from oaipmh.config import Settings
from oaipmh.requests import routes

s3 = FlaskS3()

def create_web_app(**kwargs) -> Flask: # type: ignore
    """Initialize an instance of the browse web application."""
    root = logging.getLogger()
    root.addHandler(default_handler)

    settings = Settings(**kwargs)
    settings.check()

    app = Flask('oaipmh')
    app.config.from_object(settings)

    app.engine = configure_db(settings) # type: ignore

    Base(app)

    # Log long SQL queries
    #config_query_timing(app.engine, 0.2, 8) # type: ignore

    app.register_blueprint(routes.blueprint)
    s3.init_app(app)

    @app.errorhandler(OAIException)
    def handle_oai_error(e):
        response=render_template("errors.xml", 
                response_date=datetime.now(timezone.utc),
                error=e)
        
        now = datetime.now(timezone.utc)
        seconds_until_midnight = (24 * 60 * 60) - (now.hour * 3600 + now.minute * 60 + now.second)
        headers={"Content-Type":"text/xml",
                'Surrogate-Control': f'max-age={int(seconds_until_midnight)}'
        }
        headers=add_surrogate_key(headers,["oai"])
        return response, 200, headers
    
    app.jinja_env.trim_blocks = True
    app.jinja_env.lstrip_blocks = True
    if not app.jinja_env.globals:
        app.jinja_env.globals = {}

    return app


def setup_trace(name: str, app: Flask):  # type: ignore
    """Setup GCP trace and logging."""
    from opentelemetry import trace
    from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
    from opentelemetry.instrumentation.flask import FlaskInstrumentor
    from opentelemetry.propagate import set_global_textmap
    from opentelemetry.propagators.cloud_trace_propagator import (
        CloudTraceFormatPropagator,
    )
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    # https://cloud.google.com/trace/docs/setup/python-ot#initialize_flask
    set_global_textmap(CloudTraceFormatPropagator())
    tracer_provider = TracerProvider()
    cloud_trace_exporter = CloudTraceSpanExporter()
    tracer_provider.add_span_processor(
        BatchSpanProcessor(cloud_trace_exporter)
    )
    trace.set_tracer_provider(tracer_provider)
    tracer = trace.get_tracer(name)
    FlaskInstrumentor().instrument_app(app)
    return tracer