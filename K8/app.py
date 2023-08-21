from flask import Flask
from os import path
from time import sleep
import yaml
from kubernetes import client, config

# Configure bottle
from bottle import Bottle, route, run, static_file, request, response, BaseRequest
BaseRequest.MEMFILE_MAX = 10000 * 1000

# Configure mongo connection
# https://www.mongodb.com/docs/drivers/pymongo/
import pymongo;
from pymongo.mongo_client import MongoClient
import threading
from threading import Event, Thread
import signal
import time

JOB_NAME = 'mist-reserved'

# Setup app and methods
# https://gist.github.com/richard-flosi/3789163
app = Bottle()
# app = Flask(__name__)

@app.hook('after_request')
def enable_cors():
    """
    You need to add some headers to each request.
    Don't use the wildcard '*' for Access-Control-Allow-Origin in production.
    """
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'PUT, GET, POST, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Origin, Accept, Content-Type, X-Requested-With, X-CSRF-Token'

def create_job_object():
    # Configure Pod template container
    resources = client.V1ResourceRequirements(
        limits={'nvidia.com/gpu': '1'}) # requesting 1 GPU)
    container = client.V1Container(
        #name='cuda-container',
        #image='nvcr.io/nvidia/k8s/cuda-sample:vectoradd-cuda10.2',
        name='mist-runtime',
        image='<MIST RUNTIME IMAGE>',
        resources=resources)
    
    # Configure Pod anti-affinity to prevent launching onto the same pod
    label_selector = client.V1LabelSelector(
        match_expressions=[
            {'key': 'app',
             'operator': 'In',
             'values': [JOB_NAME]}
        ])
    terms = client.V1PodAffinityTerm(
        label_selector=label_selector,
        topology_key='kubernetes.io/hostname')
    pod_anti_affinity = client.V1PodAntiAffinity(
        required_during_scheduling_ignored_during_execution=[terms])
    affinity = client.V1Affinity(pod_anti_affinity=pod_anti_affinity)
    
    # Create and configure a spec section
    template = client.V1PodTemplateSpec(
        metadata=client.V1ObjectMeta(
            name=JOB_NAME,
            labels={'app': JOB_NAME}),
        spec=client.V1PodSpec(
            affinity=affinity,
            restart_policy='OnFailure',
            containers=[container]))
    # Create the specification of deployment
    spec = client.V1JobSpec(
        parallelism=1,
        template=template,
        backoff_limit=4)
    # Instantiate the job object
    job = client.V1Job(
        api_version='batch/v1',
        kind='Job',
        metadata=client.V1ObjectMeta(name=JOB_NAME),
        spec=spec)

    return job

def create_job(api_instance, job):
    api_response = api_instance.create_namespaced_job(
        body=job,
        namespace='default')
    print('Job created. status="%s"' % str(api_response.status))

# Setup routes
@app.route('/', method='GET')
def hello_world():
    return 'Hello World!'

@app.route('/job', method='GET')
def job_create():
    config.load_incluster_config()
    batch_v1 = client.BatchV1Api()
    job = create_job_object()

    create_job(batch_v1, job)
    return 'Job created!'

# Run server
print('Start serving on port 5000')
run(app, host='0.0.0.0', port=5000, debug=True)