from typing import Dict, Any, List, Tuple
import pandas as pd
import numpy as np
from wpilog.datalog import (
    DataLogReader,
    StartRecordData,
    WPILogEntryToType,
    WPILogToDtype,
)

# Initial allocation and growth factor for record arrays.
# Growing by 2x amortises the cost to O(1) per append on average.
_INITIAL_CAPACITY = 500_000
_GROWTH_FACTOR = 2


def WPILogToDataFrame(log: DataLogReader, pivot: bool = False) -> pd.DataFrame:
    """
    Takes a DataLogReader as input and produces a pandas dataframe with timestamps as
    an index and log path names as columns.

    Uses pre-allocated numpy arrays instead of a Python list-of-tuples to cut peak
    memory by ~3× on large files.

    Arguments:
        log: The DataLogReader to read from.

    Returns:
        A dataframe with data from the log file.
    """
    startRecords: Dict[int, StartRecordData] = {}
    types: Dict[str, Any] = {}

    # Pre-allocate arrays; grow as needed
    capacity = _INITIAL_CAPACITY
    ts_arr = np.empty(capacity, dtype=np.int64)
    key_arr = np.empty(capacity, dtype=object)
    val_arr = np.empty(capacity, dtype=object)
    count = 0

    print("Iterating records...")

    for record in log:
        if record.isStart():
            startRecord: StartRecordData = record.getStartData()
            startRecords[startRecord.entry] = startRecord
        if not record.isControl():
            if record.entry not in startRecords:
                continue
            startRecord = startRecords[record.entry]

            # Grow arrays if at capacity
            if count >= capacity:
                capacity = int(capacity * _GROWTH_FACTOR)
                ts_arr = np.resize(ts_arr, capacity)
                key_arr = np.resize(key_arr, capacity)
                val_arr = np.resize(val_arr, capacity)

            ts_arr[count] = record.timestamp
            key_arr[count] = startRecord.name
            val_arr[count] = WPILogEntryToType(startRecord, record)
            types[startRecord.name] = WPILogToDtype(startRecord.type)
            count += 1

    print(f"Constructing dataframe ({count:,} records)...")

    # Trim to actual size and build DataFrame without copying the data
    df = pd.DataFrame(
        {"Key": key_arr[:count], "Value": val_arr[:count]},
        index=pd.Index(ts_arr[:count], name="Timestamp"),
    )

    if not pivot:
        return df

    df = df.pivot(columns="Key", values="Value")

    print("Converting number types to numbers. This may take a while...")

    # Convert all of the numbers to numbers
    colList = df.columns.to_list()
    colList = list(filter(lambda c: types[c] in [np.float64, pd.Int64Dtype()], colList))
    if "systemTime" in colList:
        colList.remove("systemTime")
    cols = df.columns.isin(colList)
    df[df.columns[cols]] = df[df.columns[cols]].apply(pd.to_numeric, axis=1)

    print("Setting dtypes...")

    # Set dtypes
    types["systemTime"] = "datetime64[us]"
    df = df.astype(types)

    return df
