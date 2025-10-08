import os
from decimal import *
from datetime import date
# from urllib.parse import parse_qs
from mysql.connector import connect, Error
from flask import Flask, request, Response, render_template
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
import DB_Builder
from enum import Enum

ODOPROMPTINTERVAL = 7  # the number of days to wait before prompting a regular ODO reading

app = Flask(__name__)

# function to get today's date in YYYY-mm-dd


def getDateToday():
    return date.today()


def getDateTodayStr():
    return getDateToday().strftime('%Y-%m-%d')


def strIsFloat(str=""):
    try:
        float(str)
    except ValueError:
        return False
    else:
        return True


# get Max Value for Column in table
# returns the theoretical maximum value for a given column in a given table schema
# if the data type is a decimal. (flesh this out later to more data types
# if it serves a purpose.)
def getMaxTheoValueDecimal(tableName="", columnName=""):
    result = querySQL(f"""
        SELECT numeric_precision, numeric_scale
        FROM information_schema.columns
        WHERE table_schema = "service_reminders_app"
        AND table_name = "{tableName}"
        AND column_name = "{columnName}"
        AND data_type = "decimal"
    """)
    if result == []:
        return "Column is not Decimal type"
    else:
        digitsLeftDecimal = result[0][0] - 1
        digitsRightDecimal = result[0][1]
        return 10 ** digitsLeftDecimal - 10 ** (-1 * digitsRightDecimal)

### custom exceptions ###

# exception thrown when a given row is not found in DB


class NotInDatabaseError(Exception):
    pass


class FormInputError(Exception):
    pass


class DuplicateItemError(Exception):
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

            # if the statement begins with INSERT (not case sensitive)
            # get the last inserted primary key to return.
            # and toss whatever was in the cursor before.
            if 'INSERT INTO'.lower() in stmt.lower() or \
                    'UPDATE'.lower() in stmt.lower():
                result = c1.lastrowid

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


# get eligible vehicle for the user.
def getUserUpdateVehicle(userID):
    result = querySQL(f'''
        SELECT vehicleID
        FROM vehicles
        WHERE userID = {userID}
        AND (dateLastODO IS NULL OR miles IS NULL)
        LIMIT 1
    ''')

    if result != []:
        return result[0][0]
    else:
        result = querySQL(f'''
            SELECT vehicleID FROM vehicles
            WHERE userID = {userID}
            AND DATEDIFF('{getDateToday()}', dateLastODO) > {ODOPROMPTINTERVAL}
            ORDER BY dateLastODO ASC
            LIMIT 1
        ''')
        if result == []:
            return None
        else:
            return result[0][0]


def promptUserForOneVeh(usrID=0):
    vehID = getUserUpdateVehicle(usrID)
    if vehID is None:
        raise NotInDatabaseError("no eligible vehicle found for user.")

    queryResult = querySQL(stmt=f'''
            SELECT displayName
            FROM vehicles
            WHERE vehicleID = {vehID}
    ''')

    displayName = queryResult[0]

    # we need the user name and the phone number from the user.
    queryResult = querySQL(stmt=f'''
        SELECT username, phone FROM users
        WHERE userID = {usrID}
    ''')
    (username, phone) = queryResult[0]

    msg = f"""Hey {username}, Service Reminders here. Please reply with an odometer reading for {displayName}."""

    return phone, msg


# def:
# update a vehicle odometer in database with the given odo
# this should check that the new ODO reading is greater than the previous ODO reading. Should reply to the user confirming the reading or prompting again if the reading contains an error.
# Calculate and store a new average miles per day given the prev ODO reading and the days since the last ODO reading.
def updateODO(vehID=0, newODO=0):
    today = getDateToday()

    try:
        newODO = float(newODO)
    except ValueError as e:
        raise TypeError('updateODO called with not a number.')

    if newODO is None:
        newODO = 0

    res = querySQL(f"""
        SELECT miles, dateLastODO, milesPerDay FROM vehicles WHERE vehicleID = {vehID}
    """)

    if res == []:
        raise NotInDatabaseError(
            "vehicle with this ID does not exist in the database.")

    curMiles, curOdoDate, curMilesPerDay = res[0]

    # In updateODO we want to detect if odo is None. We need to make a sepcial case.
    # and take a sepcial default action that doesn't blow up the mileage estimates.
    # In that case, let miles per day be 0 to prevent unneccesary service reminders
    # until there is a regular cadence of updates.
    # dailyMaint will check if there is no previous odo reading as well.
    # checking for null values in the other values is kind of "extra" and really
    # there just to keep things moving. I don't expect cases where these values
    # will be None in a production setting.
    if not curMiles or not curOdoDate or not curMilesPerDay:
        curMiles = 0
        curOdoDate = today
        curMilesPerDay = 0

    elif curMiles > newODO:
        raise ValueError(
            "cannot update the mileage with a lesser number than the current value")

    # we have to account for if the odo is updated again on the same day.
    try:
        newMilesPerDay = (newODO - float(curMiles)) / (today - curOdoDate).days
    except ZeroDivisionError:
        newMilesPerDay = curMilesPerDay

    querySQL(f"""
        UPDATE vehicles
        SET miles = {round(newODO, 1)}, dateLastODO = '{today}', milesPerDay = {newMilesPerDay}
        WHERE vehicleID = {vehID}
    """)


# def:
# update the records indicating a service was done at a given miles
# should remove the service due flag, update the mileage deadline, and update the ODO for the vehicle only if this ODO is greater than the ODO stored for the vehicle.
def updateServiceDone(itemID=0, itemODO=0):
    if not itemODO:  # if servODO is None
        itemODO = 0

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
    if not parentMiles or itemODO > parentMiles:
        # update the miles of the parent vehicle.
        updateODO(vehID, itemODO)

    # remove the service flag and update the dueAtMiles.
    querySQL(f"""
        UPDATE serviceSchedule
        SET dueAtMiles = {itemODO} + serviceInterval, servDueFlag = FALSE
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
        SELECT displayName FROM vehicles
        WHERE vehicleID = {vehID}
    """)

    displayName = res[0][0]

    msg = f"""
        {username}, {displayName}
        is due for item: "{desc}" at {dueAt} miles.
    """

    return phone, msg


# def:
# check the DB for service that is due and call notifyOneService for each item due.
# Send the returned message to the returned phone number by calling sendSMS
def notifyAllService():
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

    return flaggedItems


# def:
# should be called at least every day.
# check on the vehicle database, update values, and call for sending messages to the user. This should happen at a regular interval determined by the caller.
def dailyMaint():
    # get a list of userIDs which are from vehicles which have out of date ODO readings.
    query = f"""
        SELECT DISTINCT userID FROM vehicles
        WHERE DATEDIFF('{getDateTodayStr()}', dateLastODO) > '{ODOPROMPTINTERVAL}'
    """
    queryResult = querySQL(stmt=query)
    # sort the list by userID, then by dateLastODO oldest to newest. This ensures that the highest priority is to query the most out of date vehicle.
    for usr in queryResult:
        phone, msg = promptUserForOneVeh(usr[0])
        sendSMS(recip=phone, msg=msg)

    # calculate a new mileage estimate for all vehicles.
    # deal with the case in which miles is NULL.
    # if miles is NULL, estMiles and milesPerDay should be set to 0.
    # For code robustness, but not really a high-demand case, do the same
    # when milesPerDay is NULL as well we are setting estMiles so no need for that.

    # THE ORDER OF THESE QUERIES IS IMPORTANT
    queryForNull = f"""
        UPDATE vehicles
        SET estMiles = 0,
            milesPerDay = 0
        WHERE miles IS NULL OR milesPerDay IS NULL
    """
    querySQL(stmt=queryForNull)
    # now milesPerDay is never null.
    queryForNotNull = f"""
        UPDATE vehicles
        SET estMiles = (vehicles.miles +
            vehicles.milesPerDay * DATEDIFF('{getDateTodayStr()}', vehicles.dateLastODO))
        WHERE miles IS NOT NULL
    """
    querySQL(stmt=queryForNotNull)

    # for each service item, if deadline-odoEst < some constant, set the flag.
    servDueThresh = 500
    querySQL(stmt=f"""
        UPDATE serviceSchedule
        SET servDueFlag = TRUE
        WHERE (serviceSchedule.dueAtMiles - (SELECT estMiles FROM vehicles WHERE vehicles.vehicleID = serviceSchedule.vehicleID))
             < {servDueThresh}
    """)


### API Routes ###

# takes the phone number and the content and then passes the appropriate vehicleID and the content (which shoudl be odo) to the updateODO function.
@app.route("/receive_sms", methods=['POST'])
def receiveOdoMsg():
    # don't worry about any input handling except avoiding
    # SQL injection using %s and checking if the user is
    # not in the DB.
    # raises NotInDatabaseError
    def parseRequest():
        # we only care about POSTs from TWILIO so anything else can go ahead and throw some sort of exception
        # just no SQL injection, so use %s
        phone = request.form['From']
        res = querySQL(stmt="""
            SELECT userID FROM users
            WHERE phone = %s
        """, val=(phone,))
        if res == []:
            raise NotInDatabaseError(f"{phone} not in DB")

        userID = res[0][0]

        vehID = getUserUpdateVehicle(userID)
        odo = request.form['Body']

        return vehID, odo

    resp = MessagingResponse()
    maxODO = getMaxTheoValueDecimal(tableName="vehicles", columnName="miles")

    try:
        vehID, odo = parseRequest()
    except NotInDatabaseError:
        errStr = "your phone number is not associated with Service Reminders."
    else:
        if not vehID:
            errStr = "none of your vehicles need an odometer update."
        elif not strIsFloat(odo):
            errStr = "message is not a number"
        elif float(odo) < 0:
            errStr = "can't be negative."
        elif float(odo) > maxODO:
            errStr = f"number can't be more than {maxODO}"
        else:
            # lastly, try to update vehicle's ODO and check for a valueerror
            try:
                updateODO(vehID=vehID, newODO=odo)
            except ValueError:
                errStr = "must be more than your vehicle's last recorded miles."
            else:
                errStr = None

    if errStr:
        resp.message(f"Error updating Odometer: {errStr}")
    else:
        res = querySQL(
            stmt="""
                SELECT displayName FROM vehicles
                WHERE vehicleID = %s
            """,
            val=(vehID,)
        )
        displayName = res[0][0]
        resp.message(
            f"Successfully updated the odometer for {displayName}.")

    return Response(str(resp), mimetype='text/xml')


### WEB UI handler functions ###
# do all the input handling here. if bad input, raise an exception
def handleNewUserPOST():
    username = request.form['username']
    phone = request.form['phone']

    # input handling and cleaning up here.
    if 'f-you' in phone or 'whatever' in username:
        raise FormInputError('you messed up, ya doof!')

    # now check if the username or phone number already exists and raise an error for each. Can't have any duplicate phone numbers.
    res = querySQL('''
        SELECT userID FROM users
        WHERE username = %s
    ''', val=(username,))
    if res != []:
        raise DuplicateItemError('That username is already in use.')

    res = querySQL('''
        SELECT userID FROM users
        WHERE phone = %s
    ''', val=(phone,))
    if res != []:
        raise DuplicateItemError('That phone number is already in use')

    # finally, with the cleaned and validated data, add it to the database and return the cleaned data.
    try:
        newUserID = querySQL(stmt='''
            INSERT INTO users (username, phone)
            VALUES (%s, %s)
        ''', val=(username, phone))
    except Exception as e:
        # DEBUG
        raise e

    return {'userID': newUserID, 'username': username, 'phone': phone}


# in these cases, we want to check that it is a valid ID and that
# it exists in the DB.
def validateUserIdInURL(userID):
    try:
        userID = int(userID)
    except ValueError:
        raise ValueError('userID parameter not a valid userID.')

    res = querySQL(f'''
        SELECT userID FROM users
        WHERE userID = {userID}
    ''')
    if res == []:
        raise NotInDatabaseError(
            f'user with ID {userID} is not in the database.')

    return userID


# in these cases, we want to check that it is a valid ID and that
# it exists in the DB.
def validateVehIdInURL(vehID):
    try:
        vehID = int(vehID)
    except ValueError:
        raise ValueError('vehicleID parameter not valid.')

    res = querySQL(f'''
        SELECT vehicleID FROM vehicles
        WHERE vehicleID = {vehID}
    ''')
    if res == []:
        raise NotInDatabaseError(
            f'vehicle with ID {vehID} is not in the database.')

    return vehID


# validates the post request, adds data to DB,
# returns the nickname, year make model for the car
# raises exceptions if bad input
def handleNewVehiclePOST(userID):
    print(request.headers)
    print(request.form)

    try:
        userID = validateUserIdInURL(userID)
    except Exception as e:
        raise e

    # nickname
    nick = request.form['nickname']

    # year
    # check that it is present
    # check that it will convert to a YEAR type in SQL
    year = request.form['year']
    if year == '':
        raise FormInputError('Missing a year for the vehicle.')

    # try casting the input into a SQL year datatype
    res = querySQL('''
        SELECT CAST(%s AS YEAR)
    ''', val=(year, ))
    if not res[0][0]:
        raise FormInputError('please enter a valid year')

    # make
    make = request.form['make']
    if make == '':
        raise FormInputError('Missing a make for the vehicle.')

    # model
    model = request.form['model']
    if model == '':
        raise FormInputError('Missing a model for the vehicle.')

    result = querySQL(stmt='''
        INSERT INTO vehicles
        (userID, vehNickname, make, model, year)
        VALUES (%s, %s, %s, %s, %s)
    ''', val=(userID, nick, make, model, year))
    newVehID = result

    result = querySQL(stmt=f'''
        SELECT displayName FROM vehicles
        WHERE vehicleID = {newVehID}
    ''')

    dispName = result[0][0]

    # now try to add the odometer reading.
    # updateODO
    miles = request.form['miles']
    if len(miles) > 0:
        try:
            updateODO(vehID=newVehID, newODO=miles)
        except TypeError:
            raise FormInputError('miles is not a number.')
        except Exception as e:
            breakpoint()
            raise e

    # for now dont check for duplicate vehicles.

    # lastly get the username for the id so it can show
    # up in the confirmation.
    res = querySQL('''
        SELECT username FROM users
        WHERE userID = %s
    ''', val=(userID, ))
    username = res[0][0]

    return {'username': username, 'id': newVehID, 'displayName': dispName}

    # miles, if it is empty string, then leave miles NULL


# Handle the new service form, validate inputs, and add as a new service.
def handleNewServicePOST(vehicleID: int):
    print(request.headers)
    print(request.form)

    try:
        vehicleID = validateVehIdInURL(vehicleID)
    except Exception as e:
        raise e

    # description
    # check that it is present
    desc = request.form['description']
    if desc == '' or not desc:
        raise FormInputError('missing required parameter "description" in request')

    # interval
    # check that its present.
    # try casting into the data type for the column in the DB
    intv = request.form['interval']
    if intv == '' or not intv:
        raise FormInputError('missing required parameter "interval" in request')
    


    # try casting the input into a SQL year datatype
    res = querySQL('''
        SELECT CAST(%s AS YEAasdfR)
    ''', val=(year, ))
    if not res[0][0]:
        raise FormInputError('please enter a valid year')

    # make
    make = request.form['make']
    if make == '':
        raise FormInputError('Missing a make for the vehicle.')

    # model
    model = request.form['model']
    if model == '':
        raise FormInputError('Missing a model for the vehicle.')

    result = querySQL(stmt='''
        INSERT INTO vehicles
        (userID, vehNickname, make, model, year)
        VALUES (%s, %s, %s, %s, %s)
    ''', val=(userID, nick, make, model, year))
    newVehID = result

    # now try to add the odometer reading.
    # updateODO
    miles = request.form['miles']
    if len(miles) > 0:
        try:
            updateODO(vehID=newVehID, newODO=miles)
        except TypeError:
            raise FormInputError('miles is not a number.')
        except Exception as e:
            breakpoint()
            raise e

    # for now dont check for duplicate vehicles.

    # lastly get the username for the id so it can show
    # up in the confirmation.
    res = querySQL('''
        SELECT username FROM users
        WHERE userID = %s
    ''', val=(userID, ))
    username = res[0][0]

    return {'username': username, 'nick': nick, 'make': make, 'model': model, 'year': year}



### WEB UI ROUTES ###

# Serves the homepage, which consists of a welcome message
# and nav links to Home and Users


@app.route("/", methods=['GET'])
def serveHome():
    return render_template('index.html')


# USERS #

# serves the Users main page
# Which consists of a title,
# a list of users which are links to /Users/[username]
# and a link called "New User" which links to /Users/New
@app.route("/Users", methods=['GET'])
def serveUsersList():
    # retrieve a list of usernames
    res = querySQL('SELECT userID, username FROM users')
    users = []

    for item in res:
        user = {'userID': item[0], 'username': item[1]}
        users.append(user)

    return render_template('users.html', users=users)


# handles two functions in one:
@app.route("/Users/New", methods=['GET', 'POST'])
def newUserUI():
    newUserForm = 'new_user_form.html'
    newUserConf = 'new_user_submitted.html'
    if request.method == 'GET':
        return render_template(newUserForm, error=False)
    elif request.method == 'POST':
        try:
            userInfo = handleNewUserPOST()  
        except FormInputError as f:
            return render_template(newUserForm, error=True, errorMessage=str(f))
        except DuplicateItemError as d:
            return render_template(newUserForm, error=True, errorMessage=str(d))

        print(request.form)
        return render_template(newUserConf, userInfo=userInfo)
    else:
        pass


# show individual user
# Should show a list of vehicles by nickname,
# year, make model. Clicking on a vehicle takes
# you to the page for that vehicle.
# get the list of vehicles for that user,
# where each veh is a dictionary of id, nickname, make, model, year, miles.
@app.route("/Users/<userID>", methods=['GET'])
def serveSingleUserPage(userID):
    # retrieve the user given by userID, meaning a list of veh for that user.

    # get the username of the user to put in the header. Note that the userID param
    # is a user-entered value through the URL.
    try:
        userID = validateUserIdInURL(userID)
    except Exception as e:
        # if there is any issue with the input here, return page not found.
        return Response(status=404)

    res = querySQL(f'''
        SELECT username FROM users
        WHERE userID = {userID}
    ''')
    username = res[0][0]

    res = querySQL('''
        SELECT vehicleID, vehNickname, make,
            model, year, miles, dateLastOdo
        FROM vehicles
        WHERE userID = %s
    ''', val=(userID, ))
    vehicles = []

    for item in res:
        veh = {
            'id': item[0],
            'nick': item[1],
            'make': item[2],
            'model': item[3],
            'year': item[4],
            'miles': item[5],
            'dateLastOdo': item[6]
        }
        vehicles.append(veh)

    return render_template('single_user.html', user={'id': userID, 'name': username}, vehicles=vehicles)


# VEHICLES #

@app.route('/Vehicles/<vehicleID>/Update-Odometer', methods=['GET', 'POST'])
def serveUpdateOdoForm(vehicleID):
    return Response(status=200)


# should show the vehicle info in one div
# then a button to add a service item
# then another table with all the service items listed
@app.route('/Vehicles/<vehicleID>', methods=['GET'])
def serveSingleVehiclePage(vehicleID):
    try:
        vehicleID = validateVehIdInURL(vehicleID)
    except:
        return Response(status=404)
    
    res = querySQL(f'''
        SELECT vehicleID, displayName, miles, dateLastODO, estMiles
        FROM vehicles
        WHERE vehicleID = {vehicleID}
    ''')
    res = res[0]
    vehicle = {
        'id': res[0],
        'displayName': res[1], 
        'miles': res[2], 
        'dateLastODO': res[3],
        'estMiles': res[4]
    }

    res = querySQL(f'''
        SELECT description, serviceInterval, dueAtMiles
        FROM serviceSchedule
        WHERE vehicleID = {vehicleID}
    ''')

    serviceSched = []
    for result in res:
        serviceSched.append({
            'description': result[0],
            'serviceInterval': result[1],
            'dueAtMiles': result[2]
        }) 
    
    return render_template('single_vehicle.html', vehicle=vehicle, serviceSched=serviceSched)


@app.route('/Users/<userID>/New-Vehicle', methods=['GET', 'POST'])
def newVehicleUI(userID):
    newVehForm = 'new_vehicle_form.html'
    newVehConf = 'new_vehicle_submitted.html'
    try:
        userID = validateUserIdInURL(userID)
    except:
        return Response(status=404)

    if request.method == 'GET':
        return render_template(newVehForm, userID=userID, error=False)

    elif request.method == 'POST':
        try:
            vehicle = handleNewVehiclePOST(userID)
        except FormInputError as f:
            return render_template(newVehForm, userID=userID, error=True, errorMessage=str(f))
        except DuplicateItemError as d:
            return render_template(newVehForm, userID=userID, error=True, errorMessage=str(d))
        except Exception as e:
            print(e)
            return Response(status=400)

        return render_template(newVehConf, userID=userID, vehicle=vehicle)
    else:
        pass


@app.route('/Vehicles/<vehicleID>/New-Service', methods=['GET', 'POST'])
def newServiceUI(vehicleID):
    newServForm = 'new_service_form.html'
    newServConf = 'new_service_submitted.html'
    try:
        vehicleID = validateVehIdInURL(vehicleID)
    except:
        return Response(status=404)

    if request.method == 'GET':
        return render_template(newServForm, vehicleID=vehicleID, error=False)

    elif request.method == 'POST':
        try:
            servInfo = handleNewServicePOST(vehicleID)
        except FormInputError as f:
            return render_template(newServForm, vehicleID=vehicleID, error=True, errorMessage=str(f))
        except DuplicateItemError as d:
            return render_template(newServForm, vehicleID=vehicleID, error=True, errorMessage=str(d))
        except Exception as e:
            print(e)
            return Response(status=400)

        return render_template(newServConf, vehicleID=vehicleID, vehInfo=vehInfo)
    else:
        pass


### Running the server ###


# for def testing
def configHTMLAutoReload():
    app.jinja_env.auto_reload = True
    app.config['TEMPLATES_AUTO_RELOAD'] = True


if __name__ == '__main__':
    from sys import argv
    configHTMLAutoReload()
    app.run(port=3000, debug=True)

# change made in 10/8/25 remote branch.