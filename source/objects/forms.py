from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed
from objects.models import node_class
from objects.properties import node_public_properties, link_public_properties
from wtforms import *
from wtforms.validators import optional

## Object creation

class AddObjectForm(FlaskForm):
    name = TextField('Name')
    description = TextField('Description')
    location = TextField('Location')
    vendor = TextField('Vendor')

# nodes can be added to the database either one by one via the AddNode
# form, or all at once by importing an Excel or a CSV file.
class AddNode(AddObjectForm):
    node_type = [(t, t) for t in node_class]
    type = SelectField('Type', choices=node_type)
    ip_address = TextField('IP address', [optional()])
    operating_system = TextField('Operating System', [optional()])
    os_version = TextField('OS version', [optional()])
    longitude = FloatField('Longitude', [optional()])
    latitude = FloatField('Latitude', [optional()])
    secret_password = PasswordField('Secret password')

class AddNodes(FlaskForm):
    validators = [FileAllowed(['xls', 'xlsx', 'csv'], 'Excel or CSV file only')]
    file = FileField('', validators=validators)
    
class AddLink(AddObjectForm):
    source = SelectField('Source', choices=())
    destination = SelectField('Destination', choices=())

## Object deletion

class DeleteObjects(FlaskForm):
    nodes = SelectMultipleField('Nodes', choices=())
    links = SelectMultipleField('Links', choices=())

## Object filtering

def configure_form(cls):
    cls.node_properties = node_public_properties
    cls.link_properties = link_public_properties
    for property in node_public_properties:
        setattr(cls, 'node' + property, TextField(property))
        setattr(cls, 'node' + property + 'regex', BooleanField('Regex'))
    for property in link_public_properties:
        setattr(cls, 'link' + property, TextField(property))
        setattr(cls, 'link' + property + 'regex', BooleanField('Regex'))
    return cls
    
@configure_form
class FilteringForm(FlaskForm):
    pass
