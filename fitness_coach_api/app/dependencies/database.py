from fastapi import Request
from pymongo.asynchronous.database import AsyncDatabase


def get_db(req:Request)->AsyncDatabase:
    return req.app.state.mongodb