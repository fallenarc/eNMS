from apscheduler.jobstores.base import JobLookupError
from datetime import datetime, timedelta
from multiprocessing.pool import ThreadPool
from sqlalchemy import Column, ForeignKey, Integer, String, PickleType
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import relationship
from time import sleep

from eNMS import db, scheduler
from eNMS.base.associations import (
    task_log_rule_table,
    task_node_table,
    task_pool_table,
    task_workflow_table
)
from eNMS.base.custom_base import CustomBase
from eNMS.base.helpers import get_obj
from eNMS.base.properties import cls_to_properties


def job(task_name, runtime):
    with scheduler.app.app_context():
        task = get_obj(Task, name=task_name)
        task.job(runtime)


class Task(CustomBase):

    __tablename__ = 'Task'

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True)
    creation_time = Column(String)
    status = Column(String)
    type = Column(String)
    user_id = Column(Integer, ForeignKey('User.id'))
    user = relationship('User', back_populates='tasks')
    logs = Column(MutableDict.as_mutable(PickleType), default={})
    frequency = Column(String(120))
    start_date = Column(String)
    end_date = Column(String)
    x = Column(Integer, default=0)
    y = Column(Integer, default=0)
    waiting_time = Column(Integer, default=0)
    workflows = relationship(
        'Workflow',
        secondary=task_workflow_table,
        back_populates='tasks'
    )
    log_rules = relationship(
        'LogRule',
        secondary=task_log_rule_table,
        back_populates='tasks'
    )

    __mapper_args__ = {
        'polymorphic_identity': 'Task',
        'polymorphic_on': type
    }

    def __init__(self, **data):
        self.status = 'active'
        self.waiting_time = data['waiting_time']
        self.name = data['name']
        self.user = data['user']
        self.creation_time = str(datetime.now())
        self.frequency = data['frequency']
        # if the start date is left empty, we turn the empty string into
        # None as this is what AP Scheduler is expecting
        for date in ('start_date', 'end_date'):
            js_date = data[date]
            value = self.datetime_conversion(js_date) if js_date else None
            setattr(self, date, value)
        self.is_active = True
        if 'do_not_run' not in data:
            self.schedule(run_now='run_immediately' in data)

    def datetime_conversion(self, date):
        dt = datetime.strptime(date, '%d/%m/%Y %H:%M:%S')
        return datetime.strftime(dt, '%Y-%m-%d %H:%M:%S')

    def pause_task(self):
        scheduler.pause_job(self.creation_time)
        self.status = 'suspended'
        db.session.commit()

    def resume_task(self):
        scheduler.resume_job(self.creation_time)
        self.status = 'active'
        db.session.commit()

    def delete_task(self):
        try:
            scheduler.delete_job(self.creation_time)
        except JobLookupError:
            pass
        db.session.commit()

    def task_neighbors(self, workflow, type):
        return [
            x.destination for x in self.destinations
            if x.type == type and x.workflow == workflow
        ]

    def schedule(self, run_now=True):
        now = datetime.now() + timedelta(seconds=15)
        runtime = now if run_now else self.start_date
        if self.frequency:
            scheduler.add_job(
                id=self.creation_time,
                func=job,
                args=[self.name, str(runtime)],
                trigger='interval',
                start_date=runtime,
                end_date=self.end_date,
                seconds=int(self.frequency),
                replace_existing=True
            )
        else:
            scheduler.add_job(
                id=str(runtime),
                run_date=runtime,
                func=job,
                args=[self.name, str(runtime)],
                trigger='date'
            )
        return str(runtime)

    @property
    def properties(self):
        return {p: getattr(self, p) for p in cls_to_properties['Task']}


class ScriptTask(Task):

    __tablename__ = 'ScriptTask'

    id = Column(Integer, ForeignKey('Task.id'), primary_key=True)
    script_id = Column(Integer, ForeignKey('Script.id'))
    script = relationship('Script', back_populates='tasks')
    nodes = relationship(
        'Node',
        secondary=task_node_table,
        back_populates='tasks'
    )
    pools = relationship(
        'Pool',
        secondary=task_pool_table,
        back_populates='tasks'
    )

    __mapper_args__ = {
        'polymorphic_identity': 'ScriptTask',
    }

    def __init__(self, **data):
        self.script = data['job']
        self.nodes = data['nodes']
        super(ScriptTask, self).__init__(**data)

    def compute_targets(self):
        targets = set(self.nodes)
        for pool in self.pools:
            targets |= set(pool.nodes)
        return targets

    def job(self, runtime):
        results = {}
        if self.script.node_multiprocessing:
            targets = self.compute_targets()
            pool = ThreadPool(processes=len(self.nodes))
            args = [(self, node, results) for node in targets]
            pool.map(self.script.job, args)
            pool.close()
            pool.join()
            success = all(results[node.name]['success'] for node in targets)
        else:
            results = self.script.job(self, results)
            success = results['success']
        self.logs[runtime] = results
        db.session.commit()
        return success, results

    @property
    def serialized(self):
        properties = self.properties
        properties['job'] = self.script.properties if self.script else None
        properties['nodes'] = [node.properties for node in self.nodes]
        properties['pools'] = [pool.properties for pool in self.pools]
        return properties


class WorkflowTask(Task):

    __tablename__ = 'WorkflowTask'

    id = Column(Integer, ForeignKey('Task.id'), primary_key=True)
    workflow_id = Column(Integer, ForeignKey('Workflow.id'))
    workflow = relationship('Workflow', back_populates='task')

    __mapper_args__ = {
        'polymorphic_identity': 'WorkflowTask',
    }

    def __init__(self, **data):
        self.workflow = data['job']
        super(WorkflowTask, self).__init__(**data)

    def job(self, runtime):
        start_task = get_obj(Task, id=self.workflow.start_task)
        if not start_task:
            return False, {runtime: 'No start task in the workflow.'}
        layer, visited = {start_task}, set()
        result, logs = True, {}
        while layer:
            new_layer = set()
            for task in layer:
                visited.add(task)
                success, task_logs = task.job(str(datetime.now()))
                if not success:
                    result = False
                edge_type = 'success' if success else 'failure'
                for neighbor in task.task_neighbors(self.workflow, edge_type):
                    if neighbor not in visited:
                        new_layer.add(neighbor)
                logs[task.name] = task_logs
                sleep(task.waiting_time)
            layer = new_layer
        self.logs[runtime] = logs
        db.session.commit()
        return result, logs

    @property
    def serialized(self):
        properties = {p: getattr(self, p) for p in cls_to_properties['Task']}
        properties['job'] = self.workflow.properties if self.workflow else None
        return properties


def task_factory(**kwargs):
    cls = WorkflowTask if kwargs['job'].type == 'workflow' else ScriptTask
    task = get_obj(cls, name=kwargs['name'])
    if task:
        for property, value in kwargs.items():
            if property in ('start_date', 'end_date') and value:
                value = task.datetime_conversion(value)
            setattr(task, property, value)
    else:
        task = cls(**kwargs)
        db.session.add(task)
    db.session.commit()
    return task
