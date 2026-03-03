from typing import Optional, List
import os
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Depends, WebSocket, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
import hashlib
from passlib.context import CryptContext
from sqlmodel import SQLModel, Field, create_engine, Session, select
import json
from fastapi.staticfiles import StaticFiles


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        try:
            self.active_connections.remove(websocket)
        except ValueError:
            pass

    async def broadcast(self, message: dict):
        data = json.dumps(message)
        to_remove = []
        for connection in list(self.active_connections):
            try:
                await connection.send_text(data)
            except Exception:
                to_remove.append(connection)
        for c in to_remove:
            self.disconnect(c)


manager = ConnectionManager()

# Models
class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str
    hashed_password: str


class Project(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    description: Optional[str] = None
    owner_id: Optional[int] = Field(default=None, foreign_key="user.id")


class Task(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id")
    title: str
    done: bool = Field(default=False)


# DB
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./dev.db")
# For SQLite we need check_same_thread=False when using with FastAPI threads
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DATABASE_URL, echo=False)

# Auth config (prototype)
import os

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/token")


def verify_password(plain_password, hashed_password):
    return get_password_hash(plain_password) == hashed_password


def get_password_hash(password):
    # Use a simple SHA256 for prototypes to avoid bcrypt binary issues in test env.
    if isinstance(password, str):
        pw_bytes = password.encode('utf-8')[:72]
    else:
        pw_bytes = bytes(password)[:72]
    return hashlib.sha256(pw_bytes).hexdigest()


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def get_user_by_username(session: Session, username: str):
    return session.exec(select(User).where(User.username == username)).first()


def authenticate_user(session: Session, username: str, password: str):
    user = get_user_by_username(session, username)
    if not user:
        return False
    if not verify_password(password, user.hashed_password):
        return False
    return user


def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=401,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    with Session(engine) as session:
        user = get_user_by_username(session, username)
        if user is None:
            raise credentials_exception
        return user


app = FastAPI(title="Project Tracker API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    SQLModel.metadata.create_all(engine)

# Serve static frontend
try:
    app.mount("/", StaticFiles(directory="../frontend", html=True), name="frontend")
except Exception:
    # ignore if directory not present in some environments
    pass


@app.get("/", tags=["root"])
def read_root():
    return {"msg": "Project Tracker API"}


# Auth endpoints
@app.post("/register", tags=["auth"])
def register(username: str, password: str):
    with Session(engine) as session:
        existing = get_user_by_username(session, username)
        if existing:
            raise HTTPException(status_code=400, detail="Username already registered")
        user = User(username=username, hashed_password=get_password_hash(password))
        session.add(user)
        session.commit()
        session.refresh(user)
        return {"username": user.username, "id": user.id}


@app.post("/token", tags=["auth"])
def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()):
    with Session(engine) as session:
        user = authenticate_user(session, form_data.username, form_data.password)
        if not user:
            raise HTTPException(status_code=401, detail="Incorrect username or password")
        access_token = create_access_token(data={"sub": user.username}, expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
        return {"access_token": access_token, "token_type": "bearer"}


@app.get("/users/me", tags=["auth"])
def read_users_me(current_user: User = Depends(get_current_user)):
    return {"username": current_user.username, "id": current_user.id}


# Projects & tasks (creation protected)
@app.post("/projects/", response_model=Project)
def create_project(project: Project, current_user: User = Depends(get_current_user), background_tasks: BackgroundTasks = None):
    with Session(engine) as session:
        project.owner_id = current_user.id
        session.add(project)
        session.commit()
        session.refresh(project)
        # broadcast asynchronously via background task
        try:
            background_tasks.add_task(broadcast_sync, {"type": "project_created", "project": project.dict()})
        except Exception:
            pass
        return project


@app.get("/projects/", response_model=List[Project])
def list_projects(current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        projects = session.exec(select(Project).where(Project.owner_id == current_user.id)).all()
        return projects


@app.post("/projects/{project_id}/tasks/", response_model=Task)
def create_task(project_id: int, task: Task, current_user: User = Depends(get_current_user), background_tasks: BackgroundTasks = None):
    with Session(engine) as session:
        project = session.get(Project, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        if project.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not allowed to add tasks to this project")
        if task.project_id != project_id:
            task.project_id = project_id
        session.add(task)
        session.commit()
        session.refresh(task)
        try:
            background_tasks.add_task(broadcast_sync, {"type": "task_created", "task": task.dict()})
        except Exception:
            pass
        return task


@app.get("/projects/{project_id}/tasks/", response_model=List[Task])
def list_tasks(project_id: int, current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        project = session.get(Project, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        if project.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not allowed to view tasks for this project")
        tasks = session.exec(select(Task).where(Task.project_id == project_id)).all()
        return tasks


def broadcast_sync(message: dict):
    """Run the async broadcast in a sync background thread."""
    import asyncio

    try:
        asyncio.run(manager.broadcast(message))
    except Exception:
        return


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # keep connection open; clients typically don't need to send data
            await websocket.receive_text()
    except Exception:
        manager.disconnect(websocket)
