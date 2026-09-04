import os
from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader, ConsoleMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.instrumentation.asyncio import AsyncioInstrumentor
from temporalio.contrib.opentelemetry import TracingInterceptor

def setup_telemetry(service_name: str, app=None, engine=None):
    otlp_endpoint = os.getenv("OTLP_ENDPOINT")
    
    resource = Resource.create({"service.name": service_name})
    
    tracer_provider = TracerProvider(resource=resource)
    trace.set_tracer_provider(tracer_provider)
    
    meter_provider = MeterProvider(resource=resource)
    metrics.set_meter_provider(meter_provider)
    
    if otlp_endpoint:
        span_exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
        metric_exporter = OTLPMetricExporter(endpoint=otlp_endpoint)
    else:
        span_exporter = ConsoleSpanExporter()
        metric_exporter = ConsoleMetricExporter()

    span_processor = BatchSpanProcessor(span_exporter)
    tracer_provider.add_span_processor(span_processor)
    
    metric_reader = PeriodicExportingMetricReader(metric_exporter)
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)
    
    if app:
        FastAPIInstrumentor.instrument_app(app)
        
    if engine:
        SQLAlchemyInstrumentor().instrument(
            engine=engine,
        )
        
    HTTPXClientInstrumentor().instrument()
    AsyncioInstrumentor().instrument()
    
    return TracingInterceptor()