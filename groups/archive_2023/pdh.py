import pandas as pd
import numpy as np
from typing import Callable, Dict, Tuple

pd.options.mode.chained_assignment = None

def defineMetrics() -> Dict[str, Callable[[pd.DataFrame], Tuple[int, str]]]:
    """Returns a list of the metrics contained in this group and their corresponding functions."""
    return {
        "Starting Voltage": ProcessStartingVoltage,
        "Ending Voltage": ProcessEndingVoltage,
        "Max Current Draw": ProcessMaxCurrentDraw,
        "Max PDH Temperature": ProcessMaxPDHTemperature,
    }


def ProcessStartingVoltage(robotTelemetry: pd.DataFrame) -> Tuple[int, str]:
    """Process the power distribution hub voltage data.

    Uses this data to calculate the voltage the robot's battery starts at.

    Args:
        robotTelemetry: Pandas dataframe of robot telemetry

    Returns:
        A tuple containing the stoplight severity and a string containing the result of this metric.

    Raises:
        None

    """
    voltage = robotTelemetry[robotTelemetry["Key"] == "/pdh/voltage"]
    if voltage.empty:
        return -1, "metric_not_implemented"
    voltage["PDH Input Voltage (V)"] = pd.to_numeric(voltage["Value"])

    startVoltage = voltage["PDH Input Voltage (V)"].iloc[0]
    startVoltageMetricEncoding = 0
    if startVoltage < 12.0:
        startVoltageMetricEncoding = 2
    elif startVoltage < 12.15:
        startVoltageMetricEncoding = 1

    return startVoltageMetricEncoding, f"{startVoltage} V"


def ProcessEndingVoltage(robotTelemetry: pd.DataFrame) -> Tuple[int, str]:
    """Process the power distribution hub pressure voltage data.

    Uses this data to calculate the voltage the robot's battery ends at.

    Args:
        robotTelemetry: Pandas dataframe of robot telemetry

    Returns:
        A tuple containing the stoplight severity and a string containing the result of this metric.

    Raises:
        None

    """
    voltage = robotTelemetry[robotTelemetry["Key"] == "/pdh/voltage"]
    if voltage.empty:
        return -1, "metric_not_implemented"
    voltage["PDH Input Voltage (V)"] = pd.to_numeric(voltage["Value"])

    endVoltage = voltage["PDH Input Voltage (V)"].iloc[-1]
    endVoltageMetricEncoding = 0
    if endVoltage < 11.0:
        endVoltageMetricEncoding = 2
    elif endVoltage < 11.2:
        endVoltageMetricEncoding = 1

    return endVoltageMetricEncoding, f"{endVoltage} V"


def ProcessMaxCurrentDraw(robotTelemetry: pd.DataFrame) -> Tuple[int, str]:
    """Process the power distribution hub channel currents data.

    Uses this data to calculate the most current that the robot drew.

    Args:
        robotTelemetry: Pandas dataframe of robot telemetry

    Returns:
        A tuple containing the stoplight severity and a string containing the result of this metric.

    Raises:
        None

    """
    currents = robotTelemetry[robotTelemetry["Key"] == "/pdh/currents"]
    if currents.empty:
        return -1, "metric_not_implemented"
    totalCurrents = currents["Value"].to_numpy()
    # Turn the array of arrays into a 2d array
    totalCurrents = np.stack(totalCurrents)
    # Sum up the per channel current draws into one total value
    totalCurrents = totalCurrents.sum(axis=1)
    # Get the highest value (use a moving average with 50 values, or 1 second)
    maxCurrent = (np.convolve(totalCurrents, np.ones(50), "valid") / 50).max()
    stoplight = 0
    if maxCurrent > 120:
        stoplight = 2
    elif maxCurrent > (120 * 0.8) and stoplight != 2:
        stoplight = 1
    return stoplight, f"{maxCurrent} A"


def ProcessMaxPDHTemperature(robotTelemetry: pd.DataFrame) -> Tuple[int, str]:
    """Process the power distribution hub temperature data.

    Uses this data to calculate whether or not the PDH reaches concerning temperatures.

    Args:
        robotTelemetry: Pandas dataframe of robot telemetry

    Returns:
        A tuple containing the stoplight severity and a string containing the result of this metric.

    Raises:
        None

    """
    temperature = robotTelemetry[robotTelemetry["Key"] == "/pdh/temperature"]
    if temperature.empty:
        return -1, "metric_not_implemented"
    temperature["PDH Temperature (°C)"] = pd.to_numeric(temperature["Value"])

    maxTemp = temperature["PDH Temperature (°C)"].max()
    maxTempMetricEncoding = 0
    if maxTemp > 50:
        maxTempMetricEncoding = 2
    elif maxTemp > 40 and maxTempMetricEncoding != 2:
        maxTempMetricEncoding = 1

    return maxTempMetricEncoding, f"{maxTemp} °C"