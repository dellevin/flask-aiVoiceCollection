# -*- coding: utf-8 -*-
from flask import Blueprint, render_template

bp = Blueprint('chmod_calc', __name__, url_prefix='/chmod-calc')


@bp.route('/')
def page():
    return render_template('chmod_calc.html')
