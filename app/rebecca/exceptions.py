class RebeccaError(RuntimeError): pass
class RebeccaUnavailable(RebeccaError): pass
class CapabilityMissing(RebeccaError): pass
class VerificationError(RebeccaError): pass
class NotFound(RebeccaError): pass
