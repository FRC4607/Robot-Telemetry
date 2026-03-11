from typing import Set, Callable, Tuple, Dict
import pandas as pd
import numpy as np
import scipy

pd.options.mode.chained_assignment = None

boolConv = lambda x: x.replace({"true": 1, "false": 0})


def defineMetrics() -> Dict[str, Callable[[pd.DataFrame], Tuple[int, str]]]:
    """Returns a list of the metrics contained in this group and their corresponding functions."""
    return {
        "Pigeon Yaw ?Norm Error? P-val": ProcessImuYawAngleNormPigeon,
        "Pigeon Yaw DpM": ProcessImuYawAngleDriftPigeon,
    }

def ProcessImuYawAngleNormPigeon(robotTelemetry: pd.DataFrame) -> Tuple[int, str]:
    return ProcessImuYawAngleNorm("pigeon", robotTelemetry)


def ProcessImuYawAngleDriftPigeon(robotTelemetry: pd.DataFrame) -> Tuple[int, str]:
    return ProcessImuYawAngleDrift("pigeon", robotTelemetry)


def _getImuYawAngle(gyroKey: str, robotTelemetry: pd.DataFrame) -> pd.DataFrame:
    """Returns a pandas DataFrame with a column with the designated IMU's yaw angle.
    Args:
        gyroKey: Which gyro to get data frome.
        robotTelemetry: Pandas dataframe of robot telemetry

    Returns:
        A pandas dataframe with a column called "IMU Yaw Angle (deg)" that contains the values of the selected gyroscope.

    Raises:
        None
    """
    fmsMode = robotTelemetry[robotTelemetry["Key"] == "DS:enabled"]
    if fmsMode.empty:
        return pd.DataFrame()
    stopTime = fmsMode[fmsMode["Value"] == True].index.min()
    imuYawAngle = robotTelemetry[robotTelemetry["Key"] == f"swerve/{gyroKey}/yaw"]
    if imuYawAngle.empty:
        return pd.DataFrame()
    imuYawAngle = imuYawAngle[(imuYawAngle.index <= stopTime)]
    imuYawAngle["IMU Yaw Angle (deg)"] = pd.to_numeric(imuYawAngle["Value"])
    return imuYawAngle


def ProcessImuYawAngleNorm(
    gyroKey: str, robotTelemetry: pd.DataFrame
) -> Tuple[int, str]:
    """Process the IMU yaw angle telemetry data.

    Filter the IMU data to only use the samples collected while the robot is not moving and in a known postion. This
    is determined by using the `FMS Mode` telemetry and using the samples from the first `Disabled` state and the
    minimum of the `Auto` and `Teleop` states.

    Uses this data to determine whether the IMU headings follow a normmal distribution.

    Args:
        gyroKey: Which gyro to perform the test on.
        robotTelemetry: Pandas dataframe of robot telemetry

    Returns:
        A tuple containing the stoplight severity and a string containing the result of this metric.

    Raises:
        None

    """
    imuYawAngle = _getImuYawAngle(gyroKey, robotTelemetry)
    if imuYawAngle.empty:
        return -1, "metric_not_implmented"
    # Test for gaussian (gaussian means there is no drift which is good)
    gaussianMetricEncoding = 0
    stat, p = scipy.stats.shapiro(imuYawAngle["IMU Yaw Angle (deg)"].dropna())
    alpha = 0.05  # 95% confidence
    if p > alpha:
        txt = "Gaussian (fail to reject H0), p = %.3f" % (p)
    else:
        gaussianMetricEncoding = 1
        txt = "Not Gaussian (reject H0), p = %.3f" % (p)

    return (gaussianMetricEncoding, txt)


def ProcessImuYawAngleDrift(
    gyroKey: str, robotTelemetry: pd.DataFrame
) -> Tuple[int, str]:
    """Process the IMU yaw angle telemetry data.

    Filter the IMU data to only use the samples collected while the robot is not moving and in a known postion. This
    is determined by using the `FMS Mode` telemetry and using the samples from the first `Disabled` state and the
    minimum of the `Auto` and `Teleop` states.

    Uses this data to determine whether the IMU headings are severely drifting.

    Args:
        gyroKey: Which gyro to perform the test on.
        robotTelemetry: Pandas dataframe of robot telemetry

    Returns:
        A tuple containing the stoplight severity and a string containing the result of this metric.

    Raises:
        None

    """
    imuYawAngle = _getImuYawAngle(gyroKey, robotTelemetry)
    if imuYawAngle.empty:
        return -1, "metric_not_implmented"
    linearModel = np.polyfit(imuYawAngle.index, imuYawAngle["IMU Yaw Angle (deg)"], 1)
    predictor = np.poly1d(linearModel)
    imuYawAngle["Linear Regression"] = predictor(imuYawAngle.index)
    driftDegPerMinMetricEncoding = 0
    driftDegPerMin = abs(linearModel[0] * 60.0)
    if driftDegPerMin > 1.0:
        driftDegPerMinMetricEncoding = 2
    elif driftDegPerMin > 0.5:
        driftDegPerMinMetricEncoding = 1

    return driftDegPerMinMetricEncoding, str(driftDegPerMin)


def ProcessADISTemp(robotTelemetry: pd.DataFrame) -> Tuple[int, str]:
    """Processes the temperature of the Analog Devices gyroscope on the robot (if it exists) and sees if this temperature reaches concerning levels.
    Args:
        robotTelemetry: Pandas dataframe of robot telemetry

    Returns:
        A tuple containing the stoplight severity and a string containing the result of this metric.
    Raises:
        None
    """
    staleDsData = robotTelemetry[robotTelemetry["Key"] == "swerve/gyro/temp"]
    if staleDsData.empty:
        return -1, "metric_not_implemented"
    staleDsData["Gyro Temp"] = pd.to_numeric(staleDsData["Value"])

    metric = staleDsData["Gyro Temp"].max()
    metricEncoding = 0
    if metric > 45:
        metricEncoding = 2
    elif metric > 40:
        metricEncoding = 1

    return metricEncoding, str(metric)
