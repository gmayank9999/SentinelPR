"""Domain errors. Each carries the HTTP status the API layer should map it to."""


class UniErpError(Exception):
    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFound(UniErpError):
    status_code = 404


class UnknownCourseError(NotFound):
    def __init__(self, code: str):
        super().__init__(f"unknown course: {code}")
        self.code = code


class UnknownStudentError(NotFound):
    def __init__(self, student_id: str):
        super().__init__(f"unknown student: {student_id}")
        self.student_id = student_id


class ValidationError(UniErpError):
    status_code = 422


class RegistrationError(UniErpError):
    status_code = 409


class PrerequisiteCycleError(ValidationError):
    def __init__(self, cycle: list[str]):
        super().__init__("prerequisite cycle: " + " -> ".join(cycle))
        self.cycle = cycle
