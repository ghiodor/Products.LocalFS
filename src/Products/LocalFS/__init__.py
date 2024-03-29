##############################################################################
# 
# Copyright (c) 1999 Jonothan Farr 
# All rights reserved. Written by Jonothan Farr <jfarr@speakeasy.org> 
# 
# Redistribution and use in source and binary forms, with or without 
# modification, are permitted provided that the following conditions 
# are met: 
#
# 1. Redistributions of source code must retain the above copyright 
#    notice, this list of conditions and the following disclaimer. 
# 2. Redistributions in binary form must reproduce the above copyright 
#    notice, this list of conditions and the following disclaimer in the 
#    documentation and/or other materials provided with the distribution. 
# 3. The name of the author may not be used to endorse or promote products 
#    derived from this software without specific prior written permission 
# 
# Disclaimer
#
#   THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR 
#   IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES 
#   OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. 
#   IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, 
#   INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT 
#   NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, 
#   DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY 
#   THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT 
#   (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF 
#   THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE. 
#   
# In accordance with the license provided for by the software upon 
# which some of the source code has been derived or used, the following 
# acknowledgement is hereby provided: 
# 
#      "This product includes software developed by Digital Creations 
#      for use in the Z Object Publishing Environment 
#      (http://www.zope.org/)."
#
##############################################################################

__doc__="""Local File System product initialization"""
__version__='$Revision: 1.1.1.1 $'[11:-2]
    
import os
import Products.LocalFS.LocalFS
from App.ImageFile import ImageFile

misc_ = {}
icons = os.listdir(os.path.join(os.path.dirname(__file__), 'www'))
icons = filter(lambda f: f[-4:] == '.gif', icons)
for icon in icons:
    misc_[icon] = ImageFile('www/%s' % icon, globals())

def initialize(context):
    context.registerClass(
        LocalFS.LocalFS,
        constructors=(LocalFS.manage_addLocalFSForm,
                      LocalFS.manage_addLocalFS),
        icon='www/fs.gif',
        )

    context.registerHelp()
    context.registerHelpTitle('LocalFS')

