from fastcrud import FastCRUD

from .model import Job
from .schema import JobCreateInternal, JobDelete, JobRead, JobUpdate, JobUpdateInternal

CRUDJob = FastCRUD[
    Job,
    JobCreateInternal,
    JobUpdate,
    JobUpdateInternal,
    JobDelete,
    JobRead,
]
crud_jobs = CRUDJob(Job)

