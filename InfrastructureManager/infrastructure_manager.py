import json
import thread

from appscale.tools.agents.base_agent import AgentConfigurationException
from appscale.tools.agents.base_agent import AgentRuntimeException
from appscale.tools.agents.base_agent import BaseAgent
from appscale.tools.agents.factory import InfrastructureAgentFactory

from utils import utils
from utils.persistent_dictionary import PersistentDictionary
from utils.persistent_dictionary import PersistentStoreFactory

class InfrastructureManager:
  """
  InfrastructureManager class is the main entry point to the AppScale
  Infrastructure Manager implementation. An instance of this class can
  be used to start new virtual machines in a specified cloud environment
  and terminate virtual machines when they are no longer required. Instances
  of this class also keep track of the virtual machines spawned by them
  and hence each InfrastructureManager instance can be queried to obtain
  information about any virtual machines spawned by each of them in the
  past.

  This implementation is completely cloud infrastructure agnostic
  and hence can be used to spawn/terminate instances on a wide range of
  cloud (IaaS) environments. All the cloud environment specific operations
  are delegated to a separate cloud agent and the InfrastructureManager
  initializes cloud agents on demand by looking at the 'infrastructure'
  parameter passed into the methods of this class.
  """

  # Default reasons which might be returned by this module
  REASON_BAD_SECRET = 'bad secret'
  REASON_BAD_VM_COUNT = 'bad vm count'
  REASON_BAD_ARGUMENTS = 'bad arguments'
  REASON_OPERATION_ID_NOT_FOUND = 'operation_id not found'
  REASON_NONE = 'none'

  # Parameters required by InfrastructureManager
  PARAM_OPERATION_ID = 'operation_id'
  PARAM_INFRASTRUCTURE = 'infrastructure'
  PARAM_NUM_VMS = 'num_vms'

  # States a particular request could be in.
  STATE_PENDING = 'pending'
  STATE_SUCCESS = 'success'
  STATE_FAILED  = 'failed'

  # A list of parameters required to query the InfrastructureManager about
  # the state of a run_instances request.
  DESCRIBE_INSTANCES_REQUIRED_PARAMS = (PARAM_OPERATION_ID,)

  # A list of parameters required to initiate a VM deployment process
  RUN_INSTANCES_REQUIRED_PARAMS = (
    PARAM_INFRASTRUCTURE,
    PARAM_NUM_VMS
  )

  # A list of parameters required to initiate a VM termination process
  TERMINATE_INSTANCES_REQUIRED_PARAMS = ( PARAM_INFRASTRUCTURE, )

  def __init__(self, params=None, blocking=False):
    """
    Create a new InfrastructureManager instance. This constructor
    accepts an optional boolean parameter which decides whether the
    InfrastructureManager instance should operate in blocking mode
    or not. A blocking InfrastructureManager does not return until
    each requested run/terminate operation is complete. This mode
    is useful for testing and verification purposes. In a real-world
    deployment it's advisable to instantiate the InfrastructureManager
    in the non-blocking mode as run/terminate operations could take
    a rather long time to complete. By default InfrastructureManager
    instances are created in the non-blocking mode.

    Args
      params    A dictionary of parameters. Optional parameter. If
                specified it must at least include the 'store_type' parameter.
      blocking  Whether to operate in blocking mode or not. Optional
                and defaults to false.
    """
    self.blocking = blocking
    self.secret = utils.get_secret()
    self.agent_factory = InfrastructureAgentFactory()
    if params is not None:
      store_factory = PersistentStoreFactory()
      store = store_factory.create_store(params)
      self.operation_ids = PersistentDictionary(store)
    else:
      self.operation_ids = PersistentDictionary()

  def describe_operation(self, parameters, secret):
    """
    Query the InfrastructureManager instance for details regarding
    an operation id for running or terminating instances. This method accepts
    a dictionary of parameters and a secret for authentication purposes.
    The dictionary of parameters must include an 'operation_id' parameter
    which is used to lookup calls that have been made to run or terminate
    instances.

    Args:
      parameters  A dictionary of parameters which contains a valid
                  'operation_id' parameter. A valid 'operation_id'
                  is an ID issued by the run_instances method of the
                  same InfrastructureManager object. Alternatively one
                  may provide a valid JSON string instead of a dictionary
                  object.
      secret      A previously established secret

    Returns:
      invalid key or an invalid 'operation_id':
       'success': False
       'reason': is set to an error message describing the cause.

      If the provided secret key is valid and the parameters map contains
      a valid 'operation_id' parameter, this method will return a
      dictionary containing the following keys for the specified cases.

      For a run_instances operation_id:
        'success': True or False depending on the outcome of the virtual
          machine deployment process.
        'state': pending, failed, or success
        'reason': set only in a failed case.
        'vm_info': a dictionary containing the IP addresses of the spawned
          virtual machines or None if the virtual machine deployment had
          failed or still in the 'pending' state.
      For a terminate_instances operation_id:
        'success': True or False depending on the outcome of the virtual
          machine deployment process.
        'state': pending, failed, or success
        'reason': set only in a failed case.
        * note that this dictionary does not contain 'vm_info'.

    Raises:
      TypeError   If the inputs are not of the expected types
      ValueError  If the input JSON string (parameters) cannot be parsed properly
    """
    parameters, secret = self.__validate_args(parameters, secret)

    if self.secret != secret:
      return self.__generate_response(False, self.REASON_BAD_SECRET)

    for param in self.DESCRIBE_INSTANCES_REQUIRED_PARAMS:
      if not utils.has_parameter(param, parameters):
        return self.__generate_response(False, 'no ' + param)

    operation_id = parameters[self.PARAM_OPERATION_ID]
    if self.operation_ids.has_key(operation_id):
      return self.operation_ids.get(operation_id)
    else:
      return self.__generate_response(False, self.REASON_OPERATION_ID_NOT_FOUND)

  def run_instances(self, parameters, secret):
    """
    Start a new virtual machine deployment using the provided parameters. The
    input parameter set must include an 'infrastructure' parameter which indicates
    the exact cloud environment to use. Value of this parameter will be used to
    instantiate a cloud environment specific agent which knows how to interact
    with the specified cloud platform. The parameters map must also contain a
    'num_vms' parameter which indicates the number of virtual machines that should
    be spawned. In addition to that any parameters required to spawn VMs in the
    specified cloud environment must be included in the parameters map.

    If this InfrastructureManager instance has been created in the blocking mode,
    this method will not return until the VM deployment is complete. Otherwise
    this method will simply kick off the VM deployment process and return
    immediately.

    Args:
      parameters  A parameter map containing the keys 'infrastructure',
                  'num_vms' and any other cloud platform specific
                  parameters. Alternatively one may provide a valid
                  JSON string instead of a dictionary object.
      secret      A previously established secret

    Returns:
      If the secret is valid and all the required parameters are available in
      the input parameter map, this method will return a dictionary containing
      a special 'operation_id' key. If the secret is invalid or a required
      parameter is missing, this method will return a different map with the
      key 'success' set to False and 'reason' set to a simple error message.

    Raises:
      TypeError   If the inputs are not of the expected types
      ValueError  If the input JSON string (parameters) cannot be parsed properly
    """
    parameters, secret = self.__validate_args(parameters, secret)

    utils.log('Received a request to run instances.')

    if self.secret != secret:
      utils.log('Incoming secret {0} does not match the current secret {1} - '\
                'Rejecting request.'.format(secret, self.secret))
      return self.__generate_response(False, self.REASON_BAD_SECRET)

    for param in self.RUN_INSTANCES_REQUIRED_PARAMS:
      if not utils.has_parameter(param, parameters):
        return self.__generate_response(False, 'no ' + param)

    num_vms = int(parameters[self.PARAM_NUM_VMS])
    if num_vms <= 0:
      utils.log('Invalid VM count: {0}'.format(num_vms))
      return self.__generate_response(False, self.REASON_BAD_VM_COUNT)

    infrastructure = parameters[self.PARAM_INFRASTRUCTURE]
    agent = self.agent_factory.create_agent(infrastructure)
    try:
      agent.assert_required_parameters(parameters, BaseAgent.OPERATION_RUN)
    except AgentConfigurationException as exception:
      return self.__generate_response(False, str(exception))

    operation_id = utils.get_random_alphanumeric()
    status_info = {
      'success': True,
      'reason': 'received run request',
      'state': self.STATE_PENDING,
      'vm_info': None
    }
    self.operation_ids.put(operation_id, status_info)
    utils.log('Generated operation id {0} for this run '
              'instances request.'.format(operation_id))
    if self.blocking:
      self.__spawn_vms(agent, num_vms, parameters, operation_id)
    else:
      thread.start_new_thread(self.__spawn_vms,
        (agent, num_vms, parameters, operation_id))

    utils.log('Successfully started run instances request {0}.'.format(
        operation_id))
    return self.__generate_response(True,
      self.REASON_NONE, {'operation_id': operation_id})

  def terminate_instances(self, parameters, secret):
    """
    Terminate a virtual machine using the provided parameters.
    The input parameter map must contain an 'infrastructure' parameter which
    will be used to instantiate a suitable cloud agent. Any additional
    environment specific parameters should also be available in the same
    map.

    If this InfrastructureManager instance has been created in the blocking mode,
    this method will not return until the VM deployment is complete. Otherwise
    this method simply starts the VM termination process and returns immediately.

    Args:
      parameters  A dictionary of parameters containing the required
                  'infrastructure' parameter and any other platform
                  dependent required parameters. Alternatively one
                  may provide a valid JSON string instead of a dictionary
                  object.
      secret      A previously established secret

    Returns:
      If the secret is valid and all the required parameters are available in
      the input parameter map, this method will return a dictionary containing
      a special 'operation_id' key. If the secret is invalid or a required
      parameter is missing, this method will return a different map with the
      key 'success' set to False and 'reason' set to a simple error message.

    Raises:
      TypeError   If the inputs are not of the expected types
      ValueError  If the input JSON string (parameters) cannot be parsed properly
    """
    parameters, secret = self.__validate_args(parameters, secret)

    if self.secret != secret:
      return self.__generate_response(False, self.REASON_BAD_SECRET)

    for param in self.TERMINATE_INSTANCES_REQUIRED_PARAMS:
      if not utils.has_parameter(param, parameters):
        return self.__generate_response(False, 'no ' + param)

    infrastructure = parameters[self.PARAM_INFRASTRUCTURE]
    agent = self.agent_factory.create_agent(infrastructure)
    try:
      agent.assert_required_parameters(parameters,
        BaseAgent.OPERATION_TERMINATE)
    except AgentConfigurationException as exception:
      return self.__generate_response(False, str(exception))

    operation_id = utils.get_random_alphanumeric()
    status_info = {
      'success': True,
      'reason': 'received kill request',
      'state': self.STATE_PENDING
    }
    self.operation_ids.put(operation_id, status_info)
    utils.log('Generated operation id {0} for this terminate instances '
              'request.'.format(operation_id))

    if self.blocking:
      self.__kill_vms(agent, parameters, operation_id)
    else:
      thread.start_new_thread(self.__kill_vms,
                              (agent, parameters, operation_id))

    utils.log('Successfully started terminate instances request {0}.'.format(
        operation_id))
    return self.__generate_response(True,
      self.REASON_NONE, {'operation_id': operation_id})

  def attach_disk(self, parameters, disk_name, instance_id, secret):
    """ Contacts the infrastructure named in 'parameters' and tells it to
    attach a persistent disk to this machine.

    Args:
      parameters: A dict containing the credentials necessary to send requests
        to the underlying cloud infrastructure.
      disk_name: A str corresponding to the name of the persistent disk that
        should be attached to this machine.
      instance_id: A str naming the instance id that the disk should be attached
        to (typically this machine).
      secret: A str that authenticates the caller.
    """
    parameters, secret = self.__validate_args(parameters, secret)

    if self.secret != secret:
      return self.__generate_response(False, self.REASON_BAD_SECRET)

    infrastructure = parameters[self.PARAM_INFRASTRUCTURE]
    agent = self.agent_factory.create_agent(infrastructure)
    disk_location = agent.attach_disk(parameters, disk_name, instance_id)
    return self.__generate_response(True, self.REASON_NONE,
      {'location' : disk_location})


  @classmethod
  def __describe_vms(self, agent, parameters):
    """
    Private method for calling the agent to describe VMs.

    Args:
      agent           Infrastructure agent in charge of current operation
      parameters      A dictionary of parameters
    Returns:
      If the agent is able to describe instances, return the list of instance
      ids, public ips, and private ips. If the agent fails, return empty lists.
    """
    try:
      return agent.describe_instances(parameters)
    except (AgentConfigurationException, AgentRuntimeException) as exception:
      utils.log('Agent call to describe instances failed with {0}'.format(
          str(exception)))
      return [], [], []


  def __spawn_vms(self, agent, num_vms, parameters, operation_id):
    """
    Private method for starting a set of VMs

    Args:
      agent           Infrastructure agent in charge of current operation
      num_vms         No. of VMs to be spawned
      parameters      A dictionary of parameters
      operation_id  Operation ID of the current run request
    """
    status_info = self.operation_ids.get(operation_id)

    active_public_ips, active_private_ips, active_instances = \
      self.__describe_vms(agent, parameters)

    try:
      security_configured = agent.configure_instance_security(parameters)
      instance_info = agent.run_instances(num_vms, parameters,
        security_configured, public_ip_needed=False)
      ids = instance_info[0]
      public_ips = instance_info[1]
      private_ips = instance_info[2]
      status_info['state'] = self.STATE_SUCCESS
      status_info['vm_info'] = {
        'public_ips': public_ips,
        'private_ips': private_ips,
        'instance_ids': ids
      }
      utils.log('Successfully finished run instances request {0}.'.format(
          operation_id))
    except (AgentConfigurationException, AgentRuntimeException) as exception:
      # Check if we have had partial success starting instances.
      public_ips, private_ips, instance_ids = \
        self.__describe_vms(agent, parameters)

      public_ips = agent.diff(public_ips, active_public_ips)

      if public_ips:
        private_ips = agent.diff(private_ips, active_private_ips)
        instance_ids = agent.diff(instance_ids, active_instances)
        status_info['state'] = self.STATE_SUCCESS
        status_info['vm_info'] = {
          'public_ips': public_ips,
          'private_ips': private_ips,
          'instance_ids': instance_ids
        }
      else:
        status_info['state'] = self.STATE_FAILED

      # Mark it as failed either way since the AppController never checks
      # 'success' and it technically failed.
      status_info['success'] = False
      status_info['reason'] = str(exception)
      utils.log('Updating run instances request with operation id {0} to '
                'failed status because: {1}'\
                .format(operation_id, str(exception)))

    self.operation_ids.put(operation_id, status_info)


  def __kill_vms(self, agent, parameters, operation_id):
    """
    Private method for stopping a VM. This method assumes it has only been
    told to stop one VM.

    Args:
      agent       Infrastructure agent in charge of current operation
      parameters  A dictionary of parameters
      operation_id  Operation ID of the current run request
    """
    status_info = self.operation_ids.get(operation_id)
    try:
      agent.terminate_instances(parameters)
      status_info['state'] = self.STATE_SUCCESS
    except AgentRuntimeException as exception:
      status_info['state'] = self.STATE_FAILED
      status_info['reason'] = str(exception)
      utils.log('Updating terminate instances request with operation id {0} '
                'to failed status because: {1}'\
                .format(operation_id, str(exception)))

    self.operation_ids.put(operation_id, status_info)


  def __generate_response(self, status, msg, extra=None):
    """
    Generate an infrastructure manager service response

    Args:
      status  A boolean value indicating the status
      msg     A reason message (useful if this a failed operation)
      extra   Any extra fields to be included in the response (Optional)

    Returns:
      A dictionary containing the operation response
    """
    utils.log("Sending success = {0}, reason = {1}".format(status, msg))
    response = {'success': status, 'reason': msg}
    if extra is not None:
      for key, value in extra.items():
        response[key] = value
    return response

  def __validate_args(self, parameters, secret):
    """
    Validate the arguments provided by user.

    Args:
      parameters  A dictionary (or a JSON string) provided by the client
      secret      Secret sent by the client

    Returns:
      Processed user arguments

    Raises
      TypeError If at least one user argument is not of the current type
    """
    if type(parameters) != type('') and type(parameters) != type({}):
      raise TypeError('Invalid data type for parameters. Must be a '
                      'JSON string or a dictionary.')
    elif type(secret) != type(''):
      raise TypeError('Invalid data type for secret. Must be a string.')

    if type(parameters) == type(''):
      parameters = json.loads(parameters)
    return parameters, secret
