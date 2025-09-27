import os
from decimal import *
from datetime import date
# from urllib.parse import parse_qs
from mysql.connector import connect, Error
from flask import Flask, request, Response
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse

ODOPROMPTINTERVAL = 7  # the number of days to wait before prompting a regular ODO reading

app = Flask(__name__)

# global variable. this will be updated every day during the daily maintenance routine
today = date.today().strftime('%Y-%m-%d')

### custom exceptions ###


class NotInDatabaseError(Exception):
    # exception thrown when a given row is not found in DB
    pass


# function to execute SQL query in a safe container, opening and closing the connection and checking for errors along the way.
# returns the result of a query if there is one.
def querySQL(stmt="", val="", many=False):
    try:
        with connect(
            host="localhost",
            user="serv-rem-dev",
            password="password",
            database="service_reminders_app"
        ) as connection:
            c1 = connection.cursor()

            if many:
                c1.executemany(stmt, val)
            else:
                c1.execute(stmt, val)
            result = c1.fetchall()
            connection.commit()

            return result

    except Error as e:
        raise Exception(e)


def sendSMS(recip="", msg=""):
    account_sid = os.environ["TWILIO_ACCOUNT_SID"]
    auth_token = os.environ["TWILIO_AUTH_TOKEN"]
    client = Client(account_sid, auth_token)

    message = client.messages.create(
        body=msg,
        from_="+18665934611",
        to=recip,
    )


# for given user id, prompt the user for only the most out of date veh odo
# return
def promptUserForOneVeh(usrID=0):
    try:
        queryResult = querySQL(stmt=f'''
            SELECT vehNickname, make, model, year FROM vehicles WHERE userID = {usrID} ORDER BY datelastODO ASC LIMIT 1
        ''')
        (nick, make, modl, year) = queryResult[0]
    except IndexError:
        raise NotInDatabaseError(
            "called promptUserForOneVeh with a userID not in the database")

    # we need the user name and the phone number from the user.
    queryResult = querySQL(stmt=f'''
        SELECT username, phone FROM users
        WHERE userID = {usrID}
    ''')
    (username, phone) = queryResult[0]

    msg = f"""Hey {username}, Service Reminders here. Please reply with an odometer reading for {
        nick if nick != None else ' your ' + str(year) + ' ' + make + ' ' + modl}."""

    return phone, msg


# def:
# handle a received message that is an odometer reading.
# this should check that the new ODO reading is greater than the previous ODO reading. Should reply to the user confirming the reading or prompting again if the reading contains an error.
# Calculate and store a new average miles per day given the prev ODO reading and the days since the last ODO reading.
def updateODO(vehID=0, odo=0):
    if odo is None:
        odo = 0

    # alter the given vehicle record with a new odo reading. Also set the datelastodo to today.
    res = querySQL(f"""
        SELECT miles FROM vehicles WHERE vehicleID = {vehID}
    """)

    if res == []:
        raise NotInDatabaseError(
            "vehicle with this ID does not exist in the database.")

    curMiles = res[0][0]

    if not odo:
        odo = 0

    if curMiles and curMiles > odo:
        raise ValueError(
            "cannot update the mileage with a lesser number than the current value")

    querySQL(f"""
        UPDATE vehicles
        SET miles = {odo}, dateLastODO = '{today}'
        WHERE vehicleID = {vehID}
    """)


# def:
# update the records indicating a service was done at a given miles
# should remove the service due flag, update the mileage deadline, and update the ODO for the vehicle only if this ODO is greater than the ODO stored for the vehicle.
def updateServiceDone(itemID=0, servODO=0):
    if not servODO:  # if servODO is None
        servODO = 0

    # check for not the right type
    # update the miles of the parent vehicle, only if the new ODO is greater than the previous ODO.
    res = querySQL(f"""
        SELECT vehicleID, miles FROM vehicles
        WHERE vehicles.vehicleID = (SELECT vehicleID FROM serviceSchedule WHERE serviceSchedule.itemID = {itemID})
    """)
    if res == []:
        raise NotInDatabaseError(
            f"serviceSchedule record for itemID = {itemID} does not exist.")

    # yes, I know updateODO checks for this and throws an exception,
    # but this is not an error. Dont want to trip an exception.
    vehID, parentMiles = res[0]
    if not parentMiles or servODO > parentMiles:
        # update the miles of the parent vehicle.
        updateODO(vehID, servODO)

    # remove the service flag and update the dueAtMiles.
    querySQL(f"""
        UPDATE serviceSchedule
        SET dueAtMiles = {servODO} + serviceInterval, servDueFlag = FALSE
        WHERE itemID = {itemID}
    """)


# def:
# check the database for service that is due and notify the relevant user. The caller of this function sets the frequency of the reminders.
def notifyOneService(serviceItemID):
    res = querySQL(stmt=f"""
        SELECT userID, vehicleID, description, dueAtMiles FROM serviceSchedule
        WHERE itemID = {serviceItemID}
    """)
    if res == []:
        raise NotInDatabaseError(
            f"Service item {serviceItemID} was not found in the database.")
    usrID, vehID, desc, dueAt = res[0]

    res = querySQL(stmt=f"""
        SELECT username, phone FROM users
        WHERE userID = {usrID}
    """)
    username, phone = res[0]

    res = querySQL(stmt=f"""
        SELECT vehNickname, year, make, model FROM vehicles
        WHERE vehicleID = {vehID}
    """)

    nick, year, make, model = res[0]

    msg = f"""
        {username}, {nick if nick != None else ' your ' + str(year) + ' ' + str(make) + ' ' + str(model)}
        is due for item: "{desc}" at {dueAt} miles.
    """

    return phone, msg


# def:
# check the DB for service that is due and call notifyOneService for each item due.
# Send the returned message to the returned phone number by calling sendSMS
def notifyAllService():
    # query a list of flagged service items from the database
    query = """
        SELECT itemID FROM serviceSchedule
        WHERE servDueFlag = TRUE
    """
    flaggedItems = querySQL(stmt=query)

    # get the ymm and nick of the vehicle in the item
    # get the username and phone number of the user
    # {username}, your {ymm}/{nick} is due for {item} at {x} miles.
    for item in flaggedItems:
        phone, msg = notifyOneService(item[0])

        # send the message.
        sendSMS(recip=phone, msg=msg)


# def:
# SHOULD BE CALLED AT LEAST EVERY DAY or the TODAY global will not be updated.
# check on the vehicle database, update values, and call for sending messages to the user. This should happen at a regular interval determined by the caller.
def dailyMaint():
    # update today's date global variable
    global today
    today = date.today().strftime('%Y-%m-%d')  # def getTOdayt outside the func

    # Query the database for vehicles for which the last ODO reading date is more than x days ago. For this set of vehicles, call promptODO.
    query = f"""
        SELECT DISTINCT userID FROM vehicles
        WHERE DATEDIFF('{today}', dateLastODO) > '{ODOPROMPTINTERVAL}'
    """
    queryResult = querySQL(stmt=query)
    # sort the list by userID, then by dateLastODO oldest to newest. This ensures that the highest priority is to query the most out of date vehicle.
    for usr in queryResult:
        phone, msg = promptUserForOneVeh(usr[0])
        sendSMS(recip=phone, msg=msg)

    # calculate a new mileage estimate for all vehicles.
    querySQL(stmt=f"""
        UPDATE vehicles
        SET estMiles = (vehicles.miles + 
            vehicles.milesPerDay * DATEDIFF('{today}', vehicles.dateLastODO))
    """)

    # for each service item, if deadline-odoEst < some constant, set the flag.
    servDueThresh = 500
    querySQL(stmt=f"""
        UPDATE serviceSchedule
        SET servDueFlag = TRUE
        WHERE (serviceSchedule.dueAtMiles - (SELECT estMiles FROM vehicles WHERE vehicles.vehicleID = serviceSchedule.vehicleID))
             < {servDueThresh}
    """)

    # called the stored procedure which sets all service reminder flags


# takes the phone number and the content and then passes the appropriate vehicleID and the content (which shoudl be odo) to the updateODO function.
@app.route("/receive_sms", methods=['POST'])
def receiveOdoMsg():
    def parseRequest():
        def getPhone():
            return request.form['From']

        def getVehID(phone=""):
            try:
                # get a vehicle id which belongs to the user who belongs to the phone number, which is the most out of date vehicle
                res = querySQL(f"""
                    SELECT vehicleID FROM vehicles WHERE
                        (vehicles.userID=(SELECT userID FROM users WHERE users.phone={phone}))
                        AND DATEDIFF('{today}', vehicles.dateLastODO) > '{ODOPROMPTINTERVAL}'
                        ORDER BY dateLastODO ASC LIMIT 1
                """)
                return res[0]
            except Exception:
                raise Exception("There was a problem querying the database.")

        def getODO():
            result = request.form['Body']
            try:
                result = float(result)
                if result < 0:
                    raise Exception("odo value can't be negative.")
                else:
                    return result
            except ValueError:
                print(f"request body contains a non-number")
            except Exception:
                print(
                    f"An exception occurred while parsing the body of the incoming SMS")

        phone = getPhone()
        vehID = getVehID(phone)
        odo = getODO()

        return vehID, odo

    try:
        vehID, odo = parseRequest()
        resp = MessagingResponse()
        updateODO(vehID, odo)
        resp.message(f"Odomoeter for [veh] updated to {odo}.")
        # Return the TwiML (as XML) response
        return Response(str(resp), mimetype='text/xml')
    except:
        print("Error in the request or internal handling.")
        errorResponse = MessagingResponse()
        errorResponse.message("An error occurred. Try again.")
        return Response(str(errorResponse), mimetype='text/xml')


@app.route("/", methods=['GET'])
def serveHome():
    return "Service Reminders Homepage"


if __name__ == '__main__':
    # res1 = promptUserForOneVeh(usrID=0)
    try:
        updateODO(0, 0)
    except:
        raise

    from sys import argv
    # app.run(port=3000)
    # dailyMaint()
    # updateServiceDone(1, 110200.1)
    # if len(argv) == 2:
    #     run(port=int(argv[1]))
    # else:
    #     run()
