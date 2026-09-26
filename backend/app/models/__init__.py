"""
Import every model module here so they register on Base.metadata.
Required for Alembic autogenerate to see the full schema.
"""
from app.models.base import Base
from app.models.tenant import Tenant
from app.models.user import User
from app.models.agent import Agent
from app.models.role import Role, role_permissions, user_roles
from app.models.permission import Permission
from app.models.refresh_token import RefreshToken
from app.models.site import Site
from app.models.monitoring_source import MonitoringSource
from app.models.device import Device
from app.models.interface import Interface
from app.models.sensor import Sensor
from app.models.event import Event
from app.models.metric import Metric
from app.models.collector_run import CollectorRun
from app.models.business_service import BusinessService
from app.models.alert import Alert
from app.models.incident import Incident
from app.models.alert_history import AlertHistory
from app.models.maintenance_window import MaintenanceWindow
from app.models.usage import UsageCounter
from app.models.sop import SOP, SOPVersion
from app.models.runbook import Runbook
from app.models.runbook_execution import RunbookExecution
from app.models.workflow import Workflow
from app.models.workflow_execution import WorkflowExecution
from app.models.servicenow import ServiceNowConnection, ServiceNowIncidentLink, ServiceNowSyncJob
from app.models.sla import SLAPolicy, BusinessHours, HolidayCalendar
from app.models.ticket import Ticket, TicketLink, TicketComment, TicketAttachment, TicketHistory
from app.models.assignment_group import AssignmentGroup, AssignmentGroupMember
from app.models.api_key import ApiKey
from app.models.approval import ApprovalRequest, ApprovalHistory
from app.models.device_action import DeviceActionRequest, DeviceCapability
from app.models.asset_relationship import AssetRelationship
from app.models.execution import ExecutionJob, ExecutionLog, RollbackJob
from app.models.itsm import ITSMProvider, ITSMConnection, ITSMSyncJob
from app.models.knowledge_audit import KnowledgeArticle, AuditEvent
from app.models.notification_setting import NotificationSetting
from app.models.plan import Plan
from app.models.playbook import Playbook, PlaybookVersion, PlaybookStep
from app.models.processed_stripe_event import ProcessedStripeEvent
from app.models.security_setting import SecuritySetting
from app.models.setting import Setting
from app.models.sop_execution import SOPExecution
from app.models.source_health_audit import SourceHealthAudit
from app.models.subscription import Subscription
from app.models.task import Task
from app.models.noc_event import NocEvent
from app.models.integration import Integration
from app.models.normalized_event import NormalizedEvent
from app.models.zone import Zone
from app.models.change_request import ChangeRequest
from app.models.ai_diagnosis import AIDiagnosis
from app.models.resource_relationship import ResourceRelationship
from app.models.topology_link import TopologyLink
# Universal Infrastructure Intelligence layer (spec: universal resource model).
from app.models.resource_capability import ResourceCapability
from app.models.network_route import NetworkRoute
from app.models.bgp_neighbor import BgpNeighbor
# WAN Failover Intelligence layer (detect -> trace -> verify).
from app.models.operational_sla_policy import OperationalSLAPolicy
from app.models.failover_event import FailoverEvent
from app.models.failover_detector_state import FailoverDetectorState

___all__ = [
    "SOP",
    "SOPVersion",
    "Runbook",
    "RunbookExecution",
    "Workflow",
    "WorkflowExecution",
    "ServiceNowConnection",
    "ServiceNowIncidentLink",
    "ServiceNowSyncJob",
    "SLAPolicy",
    "BusinessHours",
    "HolidayCalendar",
    "Ticket",
    "TicketLink",
    "TicketComment",
    "TicketAttachment",
    "TicketHistory",
    "Alert",
    "AlertHistory",
    "Base",
    "Tenant",
    "User",
    "Agent",
    "Role",
    "Permission",
    "RefreshToken",
    "Site",
    "MonitoringSource",
    "Device",
    "Interface",
    "Sensor",
    "Event",
    "Metric",
    "CollectorRun",
    "BusinessService",
    "Incident",
    "MaintenanceWindow",
    "UsageCounter",
    "role_permissions",
    "user_roles",
    "Integration",
    "NormalizedEvent",
    "Task",
    "ResourceRelationship",
    "TopologyLink",
    "Zone",
    "ChangeRequest",
    "AIDiagnosis",
    "NocEvent",
    "ResourceCapability",
    "NetworkRoute",
    "BgpNeighbor",
    "OperationalSLAPolicy",
    "FailoverEvent",
    "FailoverDetectorState",
]
