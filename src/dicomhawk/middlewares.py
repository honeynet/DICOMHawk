from typing import Callable
from pydicom.dataset import Dataset

type Middleware = Callable[[Dataset], Dataset]

def inject_honeytoken(ds: Dataset) -> Dataset: ...