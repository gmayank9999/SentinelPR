from unierp.core.domain import Enrollment, EnrollmentStatus


def graded(code, term, grade, student_id="S1"):
    status = EnrollmentStatus.FAILED if grade == "F" else EnrollmentStatus.COMPLETED
    return Enrollment(student_id, code, term, status, grade)
