# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####

bl_info = {
    "name": "Export M3G (.m3g, .java)",
    "author": "Gerhard Völkl, Claus Hoefele, Updated for Blender 3.6",
    "version": (0, 8, 1),
    "blender": (3, 6, 0),
    "location": "File > Export > M3G",
    "description": "Export to M3G format (JSR-184)",
    "warning": "",
    "doc_url": "",
    "category": "Import-Export",
}

import bpy
import os
import sys
import struct
import zlib
from array import array
from mathutils import Vector, Matrix, Euler, Quaternion
from bpy.props import BoolProperty, StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ExportHelper
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)
from bpy.types import Operator, Panel

# ---- Helper Functions -------------------------------------------------------#
def copy_file(source, dest):
    with open(source, 'rb') as file:
        data = file.read()
    with open(dest, 'wb') as file:
        file.write(data)

def toJavaBoolean(aValue):
    return 'true' if aValue else 'false'

def sign(a):
    if a < 0: return -1
    elif a > 0: return 1
    else: return 0
     
def isOrderClockWise(v, normal):
    if type(v[0]) is bpy.types.MeshVertex:
        mNormal = TriangleNormal(Vector(v[0].co),Vector(v[1].co),Vector(v[2].co))
    else:
        mNormal = TriangleNormal(Vector(v[0]),Vectot(v[1]),Vector(v[2]))
    
    result = (sign(normal.x)==sign(mNormal.x) and
              sign(normal.y)==sign(mNormal.y) and
              sign(normal.z)==sign(mNormal.z))
    
    return True

# ---- M3G Types --------------------------------------------------------------#
class M3GVertexList:
    def __init__(self, wrapList):
        self.mlist = wrapList

    def __getitem__(self, key):
        item = self.mlist[key]
        if isinstance(item, bpy.types.MeshVertex):
            return (item.co[0], item.co[1], item.co[2])
        else:
            return item

class M3GBoneReference:
    def __init__(self, first, count):
        self.firstVertex = first  # UInt32 
        self.vertexCount = count  # UInt32 
        
class M3GBone:
    def __init__(self):
        self.verts = []  # List of influenced verts
        self.transformNode = None  # ObjectIndex
        self.references = []  # References to Verts that are needed
        self.weight = 0  # Int32

    def setVerts(self, aVerts):
        self.verts = aVerts
        self.createReferences()
        
    def createReferences(self):
        if len(self.verts) == 0: return  # No Verts available
        self.verts.sort()
        ref = []
        list = []
        last = self.verts[0]-1
        count = 0
        for vert in self.verts:
            if vert == last+1:
                list.append(vert)
            else:
                ref.append(M3GBoneReference(list[0], len(list)))
                list = [vert]
            last = vert
        if len(list) > 0:
            ref.append(M3GBoneReference(list[0], len(list)))
        self.references = ref

class M3GVector3D:
    def __init__(self, ax=0.0, ay=0.0, az=0.0):
        self.x = ax  # Float32
        self.y = ay  # Float32
        self.z = az  # Float32
    
    def writeJava(self):
        return f"{self.x}f, {self.y}f, {self.z}f"
    
    def getData(self):
        return struct.pack("<3f", self.x, self.y, self.z)
    
    def getDataLength(self):
        return struct.calcsize("<3f")

class M3GMatrix:
    def __init__(self):
        self.elements = 16 * [0.0]  # Float32
        
    def identity(self):
        self.elements[0] = 1.0
        self.elements[5] = 1.0
        self.elements[10] = 1.0
        self.elements[15] = 1.0
    
    def getData(self):
        return struct.pack('<16f', *self.elements)

    def getDataLength(self):
        return struct.calcsize('<16f')

class M3GColorRGB:
    def __init__(self, ared=0, agreen=0, ablue=0):
        self.red = ared  # Byte
        self.green = agreen  # Byte
        self.blue = ablue  # Byte
        
    def writeJava(self):
        return f"0x0000{self.red:02X}{self.green:02X}{self.blue:02X}"
    
    def getData(self):
        return struct.pack('3B', self.red, self.green, self.blue)
    
    def getDataLength(self):
        return struct.calcsize('3B')

class M3GColorRGBA:
    def __init__(self, ared=0, agreen=0, ablue=0, aalpha=0):
        self.red = ared  # Byte 
        self.green = agreen  # Byte 
        self.blue = ablue  # Byte 
        self.alpha = aalpha  # Byte

    def writeJava(self):
        return f"0x{self.alpha:02X}{self.red:02X}{self.green:02X}{self.blue:02X}"
        
    def getData(self):
        return struct.pack('4B', self.red, self.green, self.blue, self.alpha)
    
    def getDataLength(self):
        return struct.calcsize('4B')

class M3GProxy:
    def __init__(self):
        self.name = ""
        self.id = 0
        self.ObjectType = 0
        self.binaryFormat = ''
        
    def __repr__(self):
        return f"<{self.__class__.__name__}:{self.name}:{self.id}>"

class M3GHeaderObject(M3GProxy):
    def __init__(self):
        super().__init__()
        self.M3GHeaderObject_binaryFormat = '<BBBII'
        self.ObjectType = 0
        self.id = 1   # Special Object: always 1
        self.VersionNumber = [1, 0]  # Byte[2] 
        self.hasExternalReferences = False  # Boolean External Files needed? eg. Textures
        self.TotalFileSize = 0  # UInt32 
        self.ApproximateContentSize = 0  # UInt32 Only a hint! External sources included
        self.AuthoringField = 'Blender M3G Export'  # String 
        
    def getData(self):
        data = struct.pack(self.M3GHeaderObject_binaryFormat,
                           self.VersionNumber[0],
                           self.VersionNumber[1],
                           self.hasExternalReferences,
                           self.TotalFileSize,
                           self.ApproximateContentSize)
        data += struct.pack(f"{len(self.AuthoringField)+1}s", self.AuthoringField.encode())
        return data
    
    def getDataLength(self):
        value = struct.calcsize(self.M3GHeaderObject_binaryFormat)
        return value + struct.calcsize(f"{len(self.AuthoringField)+1}s")

class M3GExternalReference(M3GProxy):
    def __init__(self):         
        super().__init__()
        self.ObjectType = 0xFF
        self.URI = ''             # reference URI
        
    def getData(self):
        return struct.pack(f"{len(self.URI)+1}s", self.URI.encode())
        
    def getDataLength(self):
        return struct.calcsize(f"{len(self.URI)+1}s")
        
    def searchDeep(self, alist):
        if self not in alist: 
            alist.append(self)
        return alist
        
    def __repr__(self):
        return f"{super().__repr__()} ({self.URI})"

class M3GObject3D(M3GProxy):
    def __init__(self):
        super().__init__()
        self.userID = 0  # UInt32 - field may be any value
        self.animationTracks = []  # ObjectIndex[] 
        self.userParameterCount = 0  # UInt32 - No user parameter used 
        
    def searchDeep(self, alist):
        alist = doSearchDeep(self.animationTracks, alist)
        if self not in alist: 
            alist.append(self)
        return alist
        
    def getData(self):
        data = struct.pack('<I', self.userID)
        print(f"write userID {self.userID} {self.name} {str(self)} {self.getDataLength()}")
        data += struct.pack('<I', len(self.animationTracks))
        for element in self.animationTracks:
            data += struct.pack('<I', getId(element))
        data += struct.pack('<I', self.userParameterCount)
        return data

    def getDataLength(self):
        value = struct.calcsize('<3I')
        if len(self.animationTracks) > 0: 
            value += struct.calcsize(f'<{len(self.animationTracks)}I')
        return value

    def writeJava(self, aWriter, aCreate):
        if aCreate: 
            pass  # Abstract! Could not be created
        if len(self.animationTracks) > 0:
            aWriter.write(2)
            for iTrack in self.animationTracks:
                aWriter.write(2, f"BL{self.id}.addAnimationTrack(BL{iTrack.id});")

class M3GTransformable(M3GObject3D):
    def __init__(self):
        super().__init__()
        self.hasComponentTransform = False  # Boolean 
        self.translation = M3GVector3D(0, 0, 0)  # Vector3D 
        self.scale = M3GVector3D(1, 1, 1)  # Vector3D 
        self.orientationAngle = 0  # Float32 
        self.orientationAxis = M3GVector3D(0, 0, 0)  # Vector3D undefined
        self.hasGeneralTransform = False  # Boolean 
        self.transform = M3GMatrix()  # Matrix identity
        self.transform.identity()

    def writeJava(self, aWriter, aCreate):
        if aCreate: 
            pass  # Abstract Base Class! Can't be created
        super().writeJava(aWriter, False)
        if self.hasGeneralTransform:
            aWriter.write(2, f"float[] BL{self.id}_matrix = {{")
            aWriter.writeList(self.transform.elements, 4, "f")
            aWriter.write(2, "};")
            aWriter.write(2)
            aWriter.write(2, f"Transform BL{self.id}_transform = new Transform();")
            aWriter.write(2, f"BL{self.id}_transform.set(BL{self.id}_matrix);")
            aWriter.write(2, f"BL{self.id}.setTransform(BL{self.id}_transform);")
            aWriter.write(2)
        if self.hasComponentTransform:
            aWriter.write(2, f"BL{self.id}.setTranslation({self.translation.writeJava()});")

    def getData(self):
        data = super().getData()
        data += struct.pack("<B", self.hasComponentTransform) 
        if self.hasComponentTransform:
            data += self.translation.getData()
            data += self.scale.getData() 
            data += struct.pack('<f', self.orientationAngle) 
            data += self.orientationAxis.getData()
        data += struct.pack("<B", self.hasGeneralTransform) 
        if self.hasGeneralTransform:
            data += self.transform.getData()
        return data
        
    def getDataLength(self):
        value = super().getDataLength()
        value += struct.calcsize("<B")
        if self.hasComponentTransform:
            value += self.translation.getDataLength() 
            value += self.scale.getDataLength() 
            value += struct.calcsize('<f') 
            value += self.orientationAxis.getDataLength()
        value += struct.calcsize("<B") 
        if self.hasGeneralTransform:
            value += self.transform.getDataLength()
        return value

class M3GNode(M3GTransformable):
    def __init__(self):
        super().__init__()
        self.blenderObj = None  # Pointer to corrsponding BlenderObj 
        self.parentBlenderObj = None  # Pointer to Parent in Blender
        self.blenderMatrixWorld = None  # BlenderObj matrixWorld
        self.M3GNode_binaryFormat = '<BBBIB'
        self.enableRendering = True  # Boolean 
        self.enablePicking = True  # Boolean 
        self.alphaFactor = 255  # Byte 0x00 is equivalent to 0.0 (fully transparent), and 255 is equivalent to 1.0 (fully opaque);
        self.scope = 4294967295  # -1 #UInt32 
        self.hasAlignment = False  # Boolean 
        self.M3GNode_binaryFormat_2 = '<BBII'
        self.zTarget = 0  # Byte
        self.yTarget = 0  # Byte
        self.zReference = None  # ObjectIndex 
        self.yReference = None  # ObjectIndex 

    def getData(self):
        data = super().getData()
        data += struct.pack(self.M3GNode_binaryFormat, 
                           self.enableRendering, 
                           self.enablePicking,  
                           self.alphaFactor, 
                           self.scope,  
                           self.hasAlignment)
                            
        if self.hasAlignment:
            data += struct.pack(self.M3GNode_binaryFormat_2, 
                              self.zTarget,  
                              self.yTarget, 
                              getId(self.zReference),  
                              getId(self.yReference)) 
        return data
        
    def getDataLength(self):
        value = super().getDataLength() + struct.calcsize(self.M3GNode_binaryFormat)
        if self.hasAlignment:
            value += struct.calcsize(self.M3GNode_binaryFormat_2)
        return value
        
    def writeJava(self, aWriter, aCreate):
        if aCreate: 
            pass  # Abstract Base Class! Can't be created
        super().writeJava(aWriter, False)

class M3GGroup(M3GNode):
    def __init__(self):
        super().__init__()
        self.ObjectType = 9
        self.children = []  # ObjectIndex[] 
        
    def searchDeep(self, alist):
        for element in self.children:
            alist = element.searchDeep(alist)
        return super().searchDeep(alist)

    def writeJava(self, aWriter, aCreate):
        if aCreate:
            aWriter.write(2, f"//Group:{self.name}")
            aWriter.write(2, f"Group BL{self.id} = new Group();")
        super().writeJava(aWriter, False)
        for element in self.children:
            aWriter.write(2, f"BL{self.id}.addChild(BL{element.id});")
   
    def getData(self):
        data = super().getData()
        data += struct.pack("<I", len(self.children))
        for element in self.children:
            data += struct.pack("<I", getId(element))
        return data
    
    def getDataLength(self):
        return super().getDataLength() + struct.calcsize(f"<{len(self.children)+1}I")

class M3GWorld(M3GGroup):
    def __init__(self):
        super().__init__()
        self.ObjectType = 22
        self.activeCamera = None  # ObjectIndex 
        self.background = None  # ObjectIndex UInt32 0=None
        self.M3GWorld_binaryFormat = '<II'
        
    def searchDeep(self, alist):
        alist = doSearchDeep([self.activeCamera, self.background], alist)
        return super().searchDeep(alist)

    def writeJava(self, aWriter, aCreate):
        if aCreate:
            aWriter.write(2, f"//World:{self.name}")
            aWriter.write(2, f"World BL{self.id} = new World();")
        super().writeJava(aWriter, False)
        if self.background is not None:
            aWriter.write(2, f"BL{self.id}.setBackground(BL{self.background.id});")
        if self.activeCamera is not None:
            aWriter.write(2, f"BL{self.id}.setActiveCamera(BL{self.activeCamera.id});")
        aWriter.write(2)

    def getData(self):
        data = super().getData()
        return data + struct.pack(self.M3GWorld_binaryFormat, getId(self.activeCamera), getId(self.background))

    def getDataLength(self):
        return super().getDataLength() + struct.calcsize(self.M3GWorld_binaryFormat)

class M3GBackground(M3GObject3D):
    def __init__(self):
        super().__init__()
        self.ObjectType = 4
        self.M3GBackground_binaryFormat = '<BBiiiiBB'
        self.backgroundColor = M3GColorRGBA(0, 0, 0, 0)  # ColorRGBA 0x00000000 (black, transparent)
        self.backgroundImage = None  # ObjectIndex null (use the background color only)
        self.backgroundImageModeX = 32  # Byte BORDER=32 REPEAT=33
        self.backgroundImageModeY = 32  # Byte BORDER
        self.cropX = 0  # Int32 
        self.cropY = 0  # Int32 
        self.cropWidth = 0  # Int32 
        self.cropHeight = 0  # Int32 
        self.depthClearEnabled = True  # Boolean 
        self.colorClearEnabled = True  # Boolean 

    def writeJava(self, aWriter, aCreate):
        if aCreate:
            aWriter.write(2, f"//Background:{self.name}")
            aWriter.write(2, f"Background BL{self.id} = new Background();")
        super().writeJava(aWriter, False)
        aWriter.write(2, f"BL{self.id}.setColor({self.backgroundColor.writeJava()});")
        aWriter.write(2, "")

    def getData(self):
        data = super().getData()
        data += self.backgroundColor.getData()
        data += struct.pack('<I', getId(self.backgroundImage))
        data += struct.pack(self.M3GBackground_binaryFormat, 
                           self.backgroundImageModeX, 
                           self.backgroundImageModeY,
                           self.cropX, 
                           self.cropY, 
                           self.cropWidth, 
                           self.cropHeight, 
                           self.depthClearEnabled,  
                           self.colorClearEnabled)
        return data
    
    def getDataLength(self):
        value = super().getDataLength()
        value += self.backgroundColor.getDataLength()
        value += struct.calcsize('<I')
        value += struct.calcsize(self.M3GBackground_binaryFormat)
        return value

class M3GCamera(M3GNode):
    GENERIC = 48      # Projection-Types
    PARALLEL = 49
    PERSPECTIVE = 50
    
    def __init__(self):
        super().__init__()
        self.ObjectType = 5
        self.projectionType = M3GCamera.PARALLEL  # Byte 
        self.fovy = 0.0  # Float32 
        self.AspectRatio = 0.0  # Float32 
        self.near = 0.0  # Float32 
        self.far = 0.0  # Float32 
    
    def writeJava(self, aWriter, aCreate):
        if aCreate:
            aWriter.write(2, f"//Camera {self.name}")
            aWriter.write(2, f"Camera BL{self.id} = new Camera();")
        super().writeJava(aWriter, False)
        aWriter.write(2, f"BL{self.id}.setPerspective({self.fovy}f,  //Field of View")
        aWriter.write(4, "(float)aCanvas.getWidth()/(float)aCanvas.getHeight(),")
        aWriter.write(4, f"{self.near}f, //Near Clipping Plane")
        aWriter.write(4, f"{self.far}f); //Far Clipping Plane")              
        
    def getData(self):
        data = super().getData()
        data += struct.pack("B", self.projectionType)
        if self.projectionType == self.GENERIC:
            data += self.projectionMatrix.getData()
        else:
            data += struct.pack("<4f", self.fovy, self.AspectRatio, self.near, self.far)
        return data
    
    def getDataLength(self):
        value = super().getDataLength()
        value += struct.calcsize("B")
        if self.projectionType == self.GENERIC:
            value += self.projectionMatrix.getDataLength()
        else:
            value += struct.calcsize("<4f")
        return value

class M3GMesh(M3GNode):
    def __init__(self, aVertexBuffer=None, aIndexBuffer=[], aAppearance=[]):
        super().__init__()
        self.ObjectType = 14
        self.vertexBuffer = aVertexBuffer  # ObjectIndex 
        self.submeshCount = len(aIndexBuffer)  # UInt32 
        self.indexBuffer = aIndexBuffer  # ObjectIndex 
        self.appearance = aAppearance  # ObjectIndex 

    def getData(self):
        data = super().getData()
        data += struct.pack('<2I', getId(self.vertexBuffer), self.submeshCount)
        for i in range(len(self.indexBuffer)):
            data += struct.pack('<2I', getId(self.indexBuffer[i]), getId(self.appearance[i]))
        return data
        
    def getDataLength(self):
        value = super().getDataLength()
        value += struct.calcsize('<2I')
        for i in range(len(self.indexBuffer)):
            value += struct.calcsize('<2I')
        return value
            
    def searchDeep(self, alist):
        alist = doSearchDeep([self.vertexBuffer] + self.indexBuffer + self.appearance, alist)
        return super().searchDeep(alist)
            
    def writeJava(self, aWriter, aCreate):
        self.writeBaseJava(aWriter, aCreate, "Mesh", "")
        
    def writeBaseJava(self, aWriter, aCreate, aClassName, aExtension):
        if aCreate:
            aWriter.writeClass(aClassName, self)
            if self.submeshCount > 1:
                aWriter.write(2, f"IndexBuffer[] BL{self.id}_indexArray = {{")
                aWriter.write(4, ",".join([f"BL{i.id}" for i in self.indexBuffer]))
                aWriter.write(2, "                                };")
                aWriter.write(2)
                aWriter.write(2, f"Appearance[] BL{self.id}_appearanceArray = {{")
                aWriter.write(4, ",".join([f"BL{i.id}" for i in self.appearance]))
                aWriter.write(2, "                                };")
                aWriter.write(2)
                aWriter.write(2, f"{aClassName} BL{self.id} = new {aClassName}(BL{self.vertexBuffer.id},BL{self.id}_indexArray,BL{self.id}_appearanceArray{aExtension});")
            else:
                aWriter.write(2, f"{aClassName} BL{self.id} = new {aClassName}(BL{self.vertexBuffer.id},BL{self.indexBuffer[0].id},BL{self.appearance[0].id}{aExtension});")
        super().writeJava(aWriter, False)
        aWriter.write(2)

class M3GSkinnedMesh(M3GMesh):
    def __init__(self, aVertexBuffer=None, aIndexBuffer=[], aAppearance=[]):
        super().__init__(aVertexBuffer, aIndexBuffer, aAppearance)
        self.ObjectType = 16
        self.skeleton = None  # ObjectIndex
        self.bones = {}
        
    def searchDeep(self, alist):
        alist = doSearchDeep([self.skeleton], alist)
        return super().searchDeep(alist)

    def addSecondBone(self):
        secondBones = {}
        for bone in self.bones.values():
            bone2 = M3GBone()
            bone2.verts = bone.verts
            bone.verts = []
            mGroup = M3GGroup()
            mGroup.name = bone.transformNode.name + "_second"
            bone2.transformNode = mGroup
            bone2.references = bone.references
            bone.references = [] 
            bone2.weight = bone.weight
            bone.weight = 0
            mGroup.children = bone.transformNode.children
            bone.transformNode.children = [mGroup]
            mGroup.animationTracks = bone.transformNode.animationTracks
            bone.transformNode.animationTracks = []
            secondBones[bone.transformNode.name + "_second"] = bone2
        for bone in secondBones.values():
            self.bones[bone.transformNode.name] = bone
            
    def getBlenderIndexes(self):
        return self.vertexBuffer.positions.blenderIndexes
    
    def writeJava(self, aWriter, aCreate):
        self.writeBaseJava(aWriter, aCreate, "SkinnedMesh", f",BL{self.skeleton.id}")
        aWriter.write(2, "//Transforms")
        for bone in self.bones.values():
            for ref in bone.references:
                aWriter.write(2, f"BL{self.id}.addTransform(BL{bone.transformNode.id},{bone.weight},{ref.firstVertex},{ref.vertexCount});")
        aWriter.write(2)
        
    def getDataLength(self):
        value = super().getDataLength()
        value += struct.calcsize('<I')  # skeleton
        value += struct.calcsize('<I')  # transformReferenceCount
        for bone in self.bones.values():
            for ref in bone.references:
                value += struct.calcsize('<3Ii')
        return value
 
    def getData(self):
        data = super().getData()
        data += struct.pack('<I', getId(self.skeleton))
        count = 0
        for bone in self.bones.values(): 
            count += len(bone.references)
        data += struct.pack('<I', count)
        for bone in self.bones.values():
            for ref in bone.references:
                data += struct.pack('<I', getId(bone.transformNode))
                data += struct.pack('<2I', ref.firstVertex, ref.vertexCount)
                data += struct.pack('<i', bone.weight)
        return data

class M3GLight(M3GNode):
    def __init__(self):
        super().__init__()
        self.ObjectType = 12
        self.modes = {
            'AMBIENT': 128,
            'DIRECTIONAL': 129,
            'OMNI': 130,
            'SPOT': 131
        }
        self.attenuationConstant = 1.0  # Float32
        self.attenuationLinear = 0.0  # Float32 
        self.attenuationQuadratic = 0.0  # Float32 
        self.color = M3GColorRGB(255, 255, 255)  # ColorRGB 
        self.mode = self.modes['DIRECTIONAL']  # Byte Enumurator mode: DIRECTIONAL
        self.intensity = 1.0  # Float32 
        self.spotAngle = 45  # Float32 
        self.spotExponent = 0.0  # Float32
    
    def writeJava(self, aWriter, aCreate):
        if aCreate:
            aWriter.write(2, f"//Light: {self.name}")
            aWriter.write(2, f"Light BL{self.id} = new Light();")
        aWriter.write(2, f"BL{self.id}.setMode({self.mode});")  # Light.OMNI
        if self.mode in [self.modes['OMNI'], self.modes['SPOT']]:  # Attenuation
            aWriter.write(2, f"BL{self.id}.setAttenuation({self.attenuationConstant}f, {self.attenuationLinear}f, {self.attenuationQuadratic}f);")
        aWriter.write(2, f"BL{self.id}.setColor({self.color.writeJava()});")
        aWriter.write(2, f"BL{self.id}.setIntensity({self.intensity}f);")
        if self.mode == self.modes['SPOT']:
            aWriter.write(2, f"BL{self.id}.setSpotAngle({self.spotAngle}f);")
            aWriter.write(2, f"BL{self.id}.setSpotExponent({self.spotExponent}f);")
        super().writeJava(aWriter, False)
        aWriter.write(2)
        
    def getData(self):
        data = super().getData()
        data += struct.pack("<fff", 
                          self.attenuationConstant,
                          self.attenuationLinear, 
                          self.attenuationQuadratic) 
        data += self.color.getData() 
        data += struct.pack("<Bfff", 
                          self.mode,
                          self.intensity, 
                          self.spotAngle, 
                          self.spotExponent)
        return data

    def getDataLength(self):
        value = super().getDataLength()
        value += self.color.getDataLength()
        value += struct.calcsize('<B6f')
        return value

class M3GMaterial(M3GObject3D):
    def __init__(self):
        super().__init__()
        self.ObjectType = 13
        self.ambientColor = M3GColorRGB(51, 51, 51)  # ColorRGB 
        self.diffuseColor = M3GColorRGBA(204, 204, 204, 255)  # ColorRGBA 
        self.emissiveColor = M3GColorRGB(0, 0, 0)  # ColorRGB 
        self.specularColor = M3GColorRGB(0, 0, 0)  # ColorRGB 
        self.shininess = 0.0  # Float32 
        self.vertexColorTrackingEnabled = False  # Boolean

    def writeJava(self, aWriter, aCreate):
        if aCreate:
            aWriter.write(2, f"//Material: {self.name}")
            aWriter.write(2, f"Material BL{self.id} = new Material();")
        aWriter.write(2, f"BL{self.id}.setColor(Material.AMBIENT, {self.ambientColor.writeJava()});")
        aWriter.write(2, f"BL{self.id}.setColor(Material.SPECULAR, {self.specularColor.writeJava()});")
        aWriter.write(2, f"BL{self.id}.setColor(Material.DIFFUSE, {self.diffuseColor.writeJava()});")
        aWriter.write(2, f"BL{self.id}.setColor(Material.EMISSIVE, {self.emissiveColor.writeJava()});")
        aWriter.write(2, f"BL{self.id}.setShininess({self.shininess}f);")
        aWriter.write(2, f"BL{self.id}.setVertexColorTrackingEnable({toJavaBoolean(self.vertexColorTrackingEnabled)});")
        super().writeJava(aWriter, False)
        
    def getData(self):
        data = super().getData()
        data += self.ambientColor.getData()
        data += self.diffuseColor.getData()
        data += self.emissiveColor.getData()
        data += self.specularColor.getData()
        data += struct.pack('<fB', self.shininess, self.vertexColorTrackingEnabled)
        return data

    def getDataLength(self):
        value = super().getDataLength()
        value += self.ambientColor.getDataLength()
        value += self.diffuseColor.getDataLength()
        value += self.emissiveColor.getDataLength()
        value += self.specularColor.getDataLength()
        value += struct.calcsize('<fB')
        return value

class M3GVertexArray(M3GObject3D):
    def __init__(self, aNumComponents, aComponentSize, aAutoScaling=False, aUVMapping=False):
        super().__init__()
        self.ObjectType = 20
        self.blenderIndexes = {}  # Translation-Table from Blender index to m3g index
        self.autoscaling = aAutoScaling  # bias and scale should be computed internal
        self.uvmapping = aUVMapping  # Change coordinates from blender uv to uv-m3g
        self.bias = [0.0, 0.0, 0.0] 
        self.scale = 1.0 
        self.componentSize = aComponentSize  # Byte number of bytes per component; must be [1, 2]
        self.componentCount = aNumComponents  # Byte number of components per vertex; must be [2, 4]
        self.encoding = 0  # Byte 0="raw" as bytes or 16 bit integers.
        self.vertexCount = 0  # UInt16 number of vertices in this VertexArray; must be [1, 65535]
        if self.autoscaling:
            self.components = array('f')
        else:
            self.components = self.createComponentArray()

    def createComponentArray(self):
        return array('b') if self.componentSize == 1 else array('h')
            
    def useMaxPrecision(self, aBoundingBox):
        vertexList = M3GVertexList(aBoundingBox)
        first = vertexList[0]
        minimum = [first[0], first[1], first[2]]
        maximum = [first[0], first[1], first[2]]  # Search maximal Dimension
        
        for element in vertexList:
            for i in range(3):
                if minimum[i] > element[i]: 
                    minimum[i] = element[i]
                if maximum[i] < element[i]: 
                    maximum[i] = element[i]
        
        lrange = [0, 0, 0]
        maxRange = 0.0
        maxDimension = -1
        
        for i in range(3):  # set bias
            lrange[i] = maximum[i] - minimum[i]
            self.bias[i] = minimum[i] * 0.5 + maximum[i] * 0.5
            if lrange[i] > maxRange:
                maxRange = lrange[i]
                maxDimension = i
                
        self.scale = maxRange / 65533.0

    def internalAutoScaling(self):
        if not self.autoscaling or self.components.typecode != "f":
            return
            
        # Find bias and scale
        minimum = []
        maximum = []
        for i in range(self.componentCount):
            minimum.append(self.components[i])
            maximum.append(self.components[i])         
            
        for i in range(0, len(self.components), self.componentCount):
            for j in range(self.componentCount):
                if minimum[j] > self.components[i+j]: 
                    minimum[j] = self.components[i+j]
                if maximum[j] < self.components[i+j]: 
                    maximum[j] = self.components[i+j]
        
        lrange = [0] * self.componentCount
        maxRange = 0.0
        maxDimension = -1
        
        for i in range(self.componentCount):  # set bias
            lrange[i] = maximum[i] - minimum[i]
            self.bias[i] = minimum[i] * 0.5 + maximum[i] * 0.5
            if lrange[i] > maxRange:
                maxRange = lrange[i]
                maxDimension = i
                
        maxValue = (2 ** (8 * self.componentSize) * 1.0) - 2.0
        self.scale = maxRange / maxValue
        
        # Copy Components
        oldArray = self.components
        self.components = self.createComponentArray()
        
        for i in range(0, len(oldArray), self.componentCount):
            for j in range(self.componentCount):
                element = int((oldArray[i+j] - self.bias[j]) / self.scale)
                self.components.append(element)
        
        # Reverse t coordinate because M3G uses a different 2D coordinate system than Blender.
        if self.uvmapping:
            uv_y = self.components[i+1] * self.scale + self.bias[1]
            uv_y_flipped = 1.0 - uv_y
            self.components[i+1] = int((uv_y_flipped - self.bias[1]) / self.scale)
                
        for i in range(len(self.components)):
            if abs(self.components[i]) > maxValue:
                raise Exception(f"{i}. element too great/small!")
                
    def writeJava(self, aWriter, aCreate):
        self.internalAutoScaling()
        if aCreate:
            aWriter.write(2, f"// VertexArray {self.name}")
            if self.componentSize == 1:
                aWriter.write(2, f"byte[] BL{self.id}_array = {{")
            else:
                aWriter.write(2, f"short[] BL{self.id}_array = {{")
            aWriter.writeList(self.components)
            aWriter.write(2, "};")
            aWriter.write(2)
            aWriter.write(2, f"VertexArray BL{self.id} = new VertexArray(BL{self.id}_array.length/{self.componentCount},{self.componentCount},{self.componentSize});")
            aWriter.write(2, f"BL{self.id}.set(0,BL{self.id}_array.length/{self.componentCount},BL{self.id}_array);")
        super().writeJava(aWriter, False)
        aWriter.write(2)
     
    def getData(self):
        self.internalAutoScaling()
        self.vertexCount = len(self.components) // self.componentCount
        data = super().getData()
        data += struct.pack('<3BH', self.componentSize,
                                 self.componentCount,
                                 self.encoding,
                                 self.vertexCount)
        componentType = "b" if self.componentSize == 1 else "h"
        for element in self.components:
            data += struct.pack(f'<{componentType}', element)
        return data
        
    def getDataLength(self):
        self.internalAutoScaling()
        value = super().getDataLength()
        value += struct.calcsize('<3BH')
        componentType = "b" if self.componentSize == 1 else "h"
        value += struct.calcsize(f'<{len(self.components)}{componentType}')
        return value
        
    def append(self, element, index=None):
        if isinstance(element, Vector):
            for i in range(3):
                value = int((element[i] - self.bias[i]) / self.scale)                 
                self.components.append(value)
        elif isinstance(element, bpy.types.MeshVertex):
            for i in range(3):
                value = int((element.co[i] - self.bias[i]) / self.scale)                 
                self.components.append(value)
            if index is not None:
                key = str(len(self.blenderIndexes))
                self.blenderIndexes[key] = index
        else:
            print(f"VertexArray.append: element={element}")
            self.components.append(element)

class M3GVertexBuffer(M3GObject3D):
    def __init__(self):
        super().__init__()
        self.ObjectType = 21
        self.defaultColor = M3GColorRGBA(255, 255, 255, 255)  # ColorRGBA 0xFFFFFFFF (opaque white).
        self.positions = None  # ObjectIndex 
        self.positionBias = [0.0, 0.0, 0.0]  # Float32[3] 
        self.positionScale = 1.0  # Float32 
        self.normals = None  # ObjectIndex 
        self.colors = None  # ObjectIndex
        self.texCoordArrays = [] 
        self.texcoordArrayCount = 0  # UInt32 

    def searchDeep(self, alist):
        if self.positions is not None: 
            alist = self.positions.searchDeep(alist)
        if self.normals is not None: 
            alist = self.normals.searchDeep(alist)
        if self.colors is not None: 
            alist = self.colors.searchDeep(alist)
        alist = doSearchDeep(self.texCoordArrays, alist)
        return super().searchDeep(alist)
    
    def setPositions(self, aVertexArray):
        self.positions = aVertexArray
        self.positionBias = aVertexArray.bias
        self.positionScale = aVertexArray.scale
    
    def writeJava(self, aWriter, aCreate):
        if aCreate:
            aWriter.write(2, f"//VertexBuffer{self.name}")
            aWriter.write(2, f"VertexBuffer BL{self.id} = new VertexBuffer();")
        aWriter.write(2, f"float BL{self.id}_Bias[] = {{ {self.positionBias[0]}f, {self.positionBias[1]}f, {self.positionBias[2]}f}};")
        aWriter.write(2, f"BL{self.id}.setPositions(BL{self.positions.id},{self.positionScale}f,BL{self.id}_Bias);")
        aWriter.write(2, f"BL{self.id}.setNormals(BL{self.normals.id});")
        
        lIndex = 0
        for iTexCoord in self.texCoordArrays:
            aWriter.write(2, f"float BL{self.id}_{lIndex}_TexBias[] = {{ {iTexCoord.bias[0]}f, {iTexCoord.bias[1]}f, {iTexCoord.bias[2]}f}};")
            aWriter.write(2, f"BL{self.id}.setTexCoords({lIndex},BL{iTexCoord.id},{iTexCoord.scale}f,BL{self.id}_{lIndex}_TexBias);")
            lIndex += 1
   
        super().writeJava(aWriter, False)
    
    def getData(self):
        self.texcoordArrayCount = len(self.texCoordArrays)
        data = super().getData()
        data += self.defaultColor.getData()
        data += struct.pack('<I4f3I', 
                          getId(self.positions),
                          self.positionBias[0],
                          self.positionBias[1],
                          self.positionBias[2],
                          self.positionScale,
                          getId(self.normals),
                          getId(self.colors),
                          self.texcoordArrayCount)
        for iTexCoord in self.texCoordArrays:
            data += struct.pack('<I', getId(iTexCoord))
            data += struct.pack('<ffff', 
                              iTexCoord.bias[0],
                              iTexCoord.bias[1],
                              iTexCoord.bias[2],
                              iTexCoord.scale)
        return data

    def getDataLength(self):
        value = super().getDataLength()
        value += self.defaultColor.getDataLength()
        value += struct.calcsize('<I4f3I')
        value += struct.calcsize('<Iffff') * len(self.texCoordArrays)
        return value

class M3GPolygonMode(M3GObject3D):
    CULL_BACK = 160
    CULL_NONE = 162
    SHADE_FLAT = 164
    SHADE_SMOOTH = 165
    WINDING_CCW = 168
    WINDING_CW = 169
    
    def __init__(self):
        super().__init__()
        self.ObjectType = 8
        self.culling = M3GPolygonMode.CULL_BACK  # Byte
        self.shading = M3GPolygonMode.SHADE_SMOOTH  # Byte
        self.winding = M3GPolygonMode.WINDING_CCW  # Byte
        self.twoSidedLightingEnabled = False  # Boolean 
        self.localCameraLightingEnabled = False  # Boolean 
        self.perspectiveCorrectionEnabled = False  # Boolean
        
    def writeJava(self, aWriter, aCreate):
        if aCreate:
            aWriter.write(2, f"PolygonMode BL{self.id} = new PolygonMode();")
        aWriter.write(2, f"BL{self.id}.setCulling({self.culling});")
        aWriter.write(2, f"BL{self.id}.setShading({self.shading});")
        aWriter.write(2, f"BL{self.id}.setWinding({self.winding});")
        aWriter.write(2, f"BL{self.id}.setTwoSidedLightingEnable({toJavaBoolean(self.twoSidedLightingEnabled)});")
        aWriter.write(2, f"BL{self.id}.setLocalCameraLightingEnable({toJavaBoolean(self.localCameraLightingEnabled)});")
        aWriter.write(2, f"BL{self.id}.setPerspectiveCorrectionEnable({toJavaBoolean(self.perspectiveCorrectionEnabled)});")
        aWriter.write(2)
        super().writeJava(aWriter, False)
    
    def getData(self):
        data = super().getData()
        data += struct.pack('6B', 
                          self.culling,
                          self.shading,
                          self.winding,
                          self.twoSidedLightingEnabled, 
                          self.localCameraLightingEnabled, 
                          self.perspectiveCorrectionEnabled)
        return data

    def getDataLength(self):
        value = super().getDataLength()
        value += struct.calcsize('6B')
        return value

class M3GIndexBuffer(M3GObject3D):
    def __init__(self):
        super().__init__()

    def getData(self):
        return super().getData()
        
    def getDataLength(self):
        return super().getDataLength()
    
    def writeJava(self, aWriter, aCreate):
        super().writeJava(aWriter, False)

class M3GTriangleStripArray(M3GIndexBuffer):
    def __init__(self):
        super().__init__()
        self.ObjectType = 11 
        self.encoding = 128  # Byte Bit 7: 1 = explicit property on index buffer true
        self.indices = []  # UInt32[]
        self.stripLengths = []  # UInt32[]

    def writeJava(self, aWriter, aCreate):
        if aCreate:
            aWriter.write(2, "//length of TriangleStrips")
            aWriter.write(2, f"int[] BL{self.id}_stripLength ={{{','.join([str(element) for element in self.stripLengths])}}};")
            aWriter.write(2)
            aWriter.write(2, "//IndexBuffer")
            aWriter.write(2, f"int[] BL{self.id}_Indices = {{")
            aWriter.write(2, f"{','.join([str(element) for element in self.indices])}}};")
            aWriter.write(2)
            aWriter.write(2, f"IndexBuffer BL{self.id}=new TriangleStripArray(BL{self.id}_Indices,BL{self.id}_stripLength);")
        super().writeJava(aWriter, False)
        aWriter.write(2)
     
    def getData(self):
        data = super().getData()
        data += struct.pack('<BI', self.encoding, len(self.indices))
        for element in self.indices:
            data += struct.pack('<I', element)
        data += struct.pack('<I', len(self.stripLengths))
        for element in self.stripLengths:
            data += struct.pack('<I', element)
        return data
    
    def getDataLength(self):
        value = super().getDataLength()
        value += struct.calcsize('<BI')
        if len(self.indices) > 0:
            value += struct.calcsize(f'<{len(self.indices)}I')
        value += struct.calcsize('<I')
        if len(self.stripLengths) > 0:
            value += struct.calcsize(f'<{len(self.stripLengths)}I')
        return value

class M3GAppearance(M3GObject3D):
    def __init__(self):
        super().__init__()
        self.ObjectType = 3
        self.layer = 0  # Byte 
        self.compositingMode = None  # ObjectIndex
        self.fog = None  # ObjectIndex 
        self.polygonMode = None  # ObjectIndex 
        self.material = None  # ObjectIndex 
        self.textures = []  # ObjectIndex[]
        
    def searchDeep(self, alist):
        alist = doSearchDeep([
            self.compositingMode,
            self.fog,
            self.polygonMode,
            self.material
        ] + self.textures, alist)
        return super().searchDeep(alist)

    def getData(self):
        data = super().getData()
        data += struct.pack("<B5I", 
                          self.layer,
                          getId(self.compositingMode),
                          getId(self.fog), 
                          getId(self.polygonMode), 
                          getId(self.material), 
                          len(self.textures))
        for element in self.textures:
            data += struct.pack("<I", getId(element))
        return data
    
    def getDataLength(self):
        value = super().getDataLength()
        value += struct.calcsize("<B5I")
        if len(self.textures) > 0: 
            value += struct.calcsize(f"<{len(self.textures)}I")
        return value
        
    def writeJava(self, aWriter, aCreate):
        if aCreate:
            aWriter.write(2, "//Appearance")
            aWriter.write(2, f"Appearance BL{self.id} = new Appearance();")
        if self.compositingMode is not None:
            aWriter.write(2, f"BL{self.id}.setPolygonMode(BL{self.compositingMode.id});")
        if self.fog is not None:
            aWriter.write(2, f"BL{self.id}.setFog(BL{self.fog.id});")
        if self.polygonMode is not None:
            aWriter.write(2, f"BL{self.id}.setPolygonMode(BL{self.polygonMode.id});")
        if self.material is not None: 
            aWriter.write(2, f"BL{self.id}.setMaterial(BL{self.material.id});")
        
        i = 0
        for itexture in self.textures:
            aWriter.write(2, f"BL{self.id}.setTexture({i},BL{itexture.id});")
            i += 1
            
        super().writeJava(aWriter, False)
        aWriter.write(2)

class M3GTexture2D(M3GTransformable):
    WRAP_REPEAT = 241
    WRAP_CLAMP = 240
    FILTER_BASE_LEVEL = 208
    FILTER_LINEAR = 209
    FILTER_NEAREST = 210
    FUNC_ADD = 224
    FUNC_BLEND = 225
    FUNC_DECAL = 226
    FUNC_MODULATE = 227
    FUNC_REPLACE = 228

    def __init__(self, aImage):
        super().__init__()
        self.ObjectType = 17
        self.Image = aImage  # ObjectIndex
        self.blendColor = M3GColorRGB(0, 0, 0)
        self.blending = M3GTexture2D.FUNC_MODULATE  # Byte
        self.wrappingS = M3GTexture2D.WRAP_REPEAT  # Byte 
        self.wrappingT = M3GTexture2D.WRAP_REPEAT  # Byte 
        self.levelFilter = M3GTexture2D.FILTER_BASE_LEVEL  # Byte 
        self.imageFilter = M3GTexture2D.FILTER_NEAREST  # Byte

    def searchDeep(self, alist):
        alist = doSearchDeep([self.Image], alist)
        return super().searchDeep(alist)

    def getData(self):
        data = super().getData()
        data += struct.pack('<I', getId(self.Image))
        data += self.blendColor.getData()
        data += struct.pack('5B',
                          self.blending,
                          self.wrappingS, 
                          self.wrappingT, 
                          self.levelFilter, 
                          self.imageFilter)
        return data
    
    def getDataLength(self):
        value = super().getDataLength()
        value += struct.calcsize('<I')
        value += self.blendColor.getDataLength()
        value += struct.calcsize('5B')
        return value
            
    def writeJava(self, aWriter, aCreate):
        if aCreate:
            aWriter.write(2, "//Texture")
            aWriter.write(2, f"Texture2D BL{self.id} = new Texture2D(BL{self.Image.id});")
        aWriter.write(2, f"BL{self.id}.setFiltering({self.levelFilter},{self.imageFilter});")
        aWriter.write(2, f"BL{self.id}.setWrapping({self.wrappingS},{self.wrappingT});")
        aWriter.write(2, f"BL{self.id}.setBlending({self.blending});")
        aWriter.write(2)
        super().writeJava(aWriter, False)

class ImageFactory:
    images = {}
    
    @classmethod
    def getImage(cls, image, externalReference):
        filename = bpy.path.abspath(image.filepath)
        
        if filename in cls.images:
            return cls.images[filename]
        elif externalReference:
            # Check for file ending (only relevant for external images)
            ext = os.path.splitext(filename)[1].lower()
            if ext != ".png":
                print(f"Warning: image file ends with {ext}. M3G specification only mandates PNG support.")

            image_ref = M3GExternalReference()
            image_ref.URI = os.path.basename(filename)
            cls.images[filename] = image_ref
        else:
            image_ref = M3GImage2D(image)
            cls.images[filename] = image_ref
        return image_ref

class M3GImage2D(M3GObject3D):
    ALPHA = 96             # a single byte per pixel, representing pixel opacity
    LUMINANCE = 97         # a single byte per pixel, representing pixel luminance.
    LUMINANCE_ALPHA = 98   # two bytes per pixel. The first: luminance, the second: alpha.
    RGB = 99               # three bytes per pixel, representing red, green and blue
    RGBA = 100             # four bytes per pixel, representing red, green, blue and alpha

    def __init__(self, aImage, aFormat=RGBA):
        super().__init__()
        self.ObjectType = 10
        self.image = aImage  # Blender Image
        self.format = aFormat  # Byte 
        self.isMutable = False  # Boolean changable or not
        self.width, self.height = aImage.size
        self.palette = 0  # Byte[] 
        self.pixels = array('B')  # Byte[] 
        self.extractPixelsFromImage()

    def getData(self):
        data = super().getData()
        data += struct.pack('2B', self.format, self.isMutable)
        data += struct.pack('<2I', self.width, self.height)
        if not self.isMutable:
            # TODO: support palettised formats also
            # export palette data
            data += struct.pack('<I', 0)
            
            # export pixel data
            if self.format == M3GImage2D.RGBA:
                data += struct.pack('<I', len(self.pixels))
                for pixel in self.pixels:
                    data += struct.pack('B', pixel)
        return data

    def getDataLength(self):
        value = super().getDataLength()
        value += struct.calcsize('2B')
        value += struct.calcsize('<2I')
        if not self.isMutable:
            # TODO: support palettised formats also
            value += struct.calcsize('<I')
            
            # pixel data size
            if self.format == M3GImage2D.RGBA:
                value += struct.calcsize('<I')
                value += struct.calcsize(f'{len(self.pixels)}B')
        return value
    
    def writeJava(self, aWriter, aCreate):
        if aCreate:
            lFileName = bpy.path.abspath(self.image.filepath)
            if not os.path.exists(lFileName):
                lFileName = os.path.join(os.path.dirname(bpy.data.filepath), os.path.basename(self.image.filepath))
            
            if not os.path.exists(lFileName):
                raise Exception('Image file not found!')
                
            lTargetFile = os.path.join(os.path.dirname(aWriter.filename), os.path.basename(self.image.filepath))   
            copy_file(lFileName, lTargetFile)
            
            aWriter.write(2, "//Image2D")
            aWriter.write(2, f"Image BL{self.id}_Image = null;")
            aWriter.write(2, "try {")
            aWriter.write(3, f'BL{self.id}_Image = Image.createImage("/{os.path.basename(self.image.filepath)}");')
            aWriter.write(2, "} catch (IOException e) {")
            aWriter.write(3, "e.printStackTrace();")
            aWriter.write(2, "}")
            aWriter.write(2, f"Image2D BL{self.id} = new Image2D(Image2D.RGBA,BL{self.id}_Image);")   
        aWriter.write(2)
        super().writeJava(aWriter, False)
        aWriter.write(2)
        
    def extractPixelsFromImage(self):
        # Reverse t coordinate because M3G uses a different 2D coordinate system than OpenGL.
        pixels = self.image.pixels[:]
        for y in range(self.height):
            for x in range(self.width):
                idx = (y * self.width + x) * 4
                r = int(pixels[idx] * 255)
                g = int(pixels[idx+1] * 255)
                b = int(pixels[idx+2] * 255)
                a = int(pixels[idx+3] * 255)
                self.pixels.append(r)
                self.pixels.append(g)
                self.pixels.append(b)
                self.pixels.append(a)

class M3GAnimationController(M3GObject3D):
    def __init__(self):
        super().__init__()
        self.ObjectType = 1
        self.speed = 1.0  # Float32
        self.weight = 1.0  # Float32
        self.activeIntervalStart = 0  # Int32 - (always active)
        self.activeIntervalEnd = 0  # Int32 
        self.referenceSequenceTime = 0.0  # Float32 
        self.referenceWorldTime = 0  # Int32 

    def writeJava(self, aWriter, aCreate):
        if aCreate:
            aWriter.writeClass("AnimationController", self)
            aWriter.write(2, f"AnimationController BL{self.id} = new AnimationController();")
        aWriter.write(2, f"BL{self.id}.setActiveInterval({self.activeIntervalStart}, {self.activeIntervalEnd});")
        super().writeJava(aWriter, False)
            
    def getData(self):
        data = super().getData()
        data += struct.pack("<ffiifi", 
                          self.speed,
                          self.weight,
                          self.activeIntervalStart,
                          self.activeIntervalEnd, 
                          self.referenceSequenceTime, 
                          self.referenceWorldTime)
        return data
        
    def getDataLength(self):
        value = super().getDataLength()
        return value + struct.calcsize("<ffiifi")

class M3GAnimationTrack(M3GObject3D):
    ALPHA = 256
    AMBIENT_COLOR = 257
    COLOR = 258
    CROP = 259
    DENSITY = 260
    DIFFUSE_COLOR = 261
    EMISSIVE_COLOR = 262
    FAR_DISTANCE = 263
    FIELD_OF_VIEW = 264
    INTENSITY = 265
    MORPH_WEIGHTS = 266
    NEAR_DISTANCE = 267
    ORIENTATION = 268
    PICKABILITY = 269
    SCALE = 270
    SHININESS = 271
    SPECULAR_COLOR = 272
    SPOT_ANGLE = 273
    SPOT_EXPONENT = 274
    TRANSLATION = 275
    VISIBILITY = 276

    def __init__(self, aSequence, aProperty):
        super().__init__()
        self.ObjectType = 2
        self.keyframeSequence = aSequence  # ObjectIndex 
        self.animationController = None  # ObjectIndex
        self.propertyID = aProperty  # UInt32 
    
    def getData(self):
        data = super().getData()
        data += struct.pack("<3I", 
                          getId(self.keyframeSequence),
                          getId(self.animationController),
                          self.propertyID)
        return data
        
    def getDataLength(self):
        value = super().getDataLength()
        return value + struct.calcsize("<3I")
            
    def writeJava(self, aWriter, aCreate):
        if aCreate:
            aWriter.writeClass("AnimationTrack", self)
            aWriter.write(2, f"AnimationTrack BL{self.id} = new AnimationTrack(BL{self.keyframeSequence.id},{self.propertyID});")
        aWriter.write(2, f"BL{self.id}.setController(BL{self.animationController.id});")
        super().writeJava(aWriter, False)
        
    def searchDeep(self, alist):
        alist = doSearchDeep([self.keyframeSequence, self.animationController], alist)
        return super().searchDeep(alist)

class M3GKeyframeSequence(M3GObject3D):
    CONSTANT = 192
    LINEAR = 176
    LOOP = 193
    SLERP = 177
    SPLINE = 178
    SQUAD = 179
    STEP = 180
        
    def __init__(self, aNumKeyframes, aNumComponents, aBlenderInterpolation, aM3GInterpolation=None):
        super().__init__()
        self.ObjectType = 19
        if aM3GInterpolation is not None:
            self.interpolation = aM3GInterpolation
        else:
            if aBlenderInterpolation == "Constant":
                self.interpolation = self.STEP  # Byte 
            elif aBlenderInterpolation == "Bezier":
                self.interpolation = self.SPLINE  # Byte 
            elif aBlenderInterpolation == "Linear":
                self.interpolation = self.LINEAR  # Byte 
            else:
                pass  # TODO: Throw Error
                
        self.repeatMode = self.CONSTANT  # Byte CONSTANT or LOOP
        self.encoding = 0  # Byte 0=raw 
        self.duration = 0  # UInt32 
        self.validRangeFirst = 0  # UInt32 
        self.validRangeLast = 0  # UInt32 
        self.componentCount = aNumComponents  # UInt32
        self.keyframeCount = aNumKeyframes  # UInt32  
        self.time = []  # Int32
        self.vectorValue = []  # Float32[componentCount]

    def beforeExport(self):
        # M3G can not work with negative zero
        for i in range(self.keyframeCount):
            for j in range(self.componentCount):
                if abs(self.vectorValue[i][j]) < 0.000001:
                    self.vectorValue[i][j] = 0.0
    
    def getData(self):
        self.beforeExport()
        data = super().getData()
        data += struct.pack("<3B5I",
                          self.interpolation, 
                          self.repeatMode,
                          self.encoding, 
                          self.duration, 
                          self.validRangeFirst, 
                          self.validRangeLast, 
                          self.componentCount,
                          self.keyframeCount) 
        # FOR each key frame...
        for i in range(self.keyframeCount):
            data += struct.pack("<i", self.time[i])  # Int32
            for j in range(self.componentCount):
                data += struct.pack("<f", self.vectorValue[i][j])  # Float32[componentCount]
        return data

    def getDataLength(self):
        value = super().getDataLength()
        value += struct.calcsize("<3B5I")
        value += struct.calcsize("<i") * self.keyframeCount
        value += struct.calcsize("<f") * self.keyframeCount * self.componentCount
        return value
        
    def setRepeatMode(self, aBlenderMode):
        if aBlenderMode == "Constant":
            self.repeatMode = self.CONSTANT
        elif aBlenderMode == "Cyclic":
            self.repeatMode = self.LOOP
        else:
            print(f"In IPO: Mode {aBlenderMode} is not assisted!")

    def setKeyframe(self, aIndex, aTime, aVector):
        self.time.append(aTime)
        self.vectorValue.append(aVector)
            
    def writeJava(self, aWriter, aCreate):
        self.beforeExport()
        if aCreate:
            aWriter.writeClass("KeyframeSequence", self)
            aWriter.write(2, f"KeyframeSequence BL{self.id} = new KeyframeSequence({self.keyframeCount}, {self.componentCount}, {self.interpolation});")
            for i in range(len(self.time)):
                lLine = f"BL{self.id}.setKeyframe({i},{self.time[i]}, new float[] {{ {self.vectorValue[i][0]}f, {self.vectorValue[i][1]}f, {self.vectorValue[i][2]}f"
                if self.componentCount == 4:
                    lLine += f", {self.vectorValue[i][3]}f"
                lLine += "});"
                aWriter.write(2, lLine)
        aWriter.write(2, f"BL{self.id}.setDuration({self.duration});")
        aWriter.write(2, f"BL{self.id}.setRepeatMode({self.repeatMode});")
        super().writeJava(aWriter, False)

# ---- Translator -------------------------------------------------------------- #
class M3GTranslator:
    def __init__(self, context):
        self.context = context
        self.world = None
        self.scene = None
        self.nodes = []
    
    def start(self):
        print("Translate started ...")
        
        self.scene = self.context.scene
        self.world = self.translateWorld(self.scene)
        
        for obj in self.scene.objects:
            if obj.type == 'CAMERA':
                self.translateCamera(obj)
            elif obj.type == 'MESH':
                self.translateMesh(obj)
            elif obj.type == 'LIGHT' and self.context.scene.m3g_export_props.lightingEnabled:
                self.translateLamp(obj)
            elif obj.type == 'EMPTY':
                self.translateEmpty(obj)
            else:
                print(f"Warning: could not translate {str(obj)}. Try to convert object to mesh using Alt-C")
                
        self.translateParenting()
            
        print("Translate finished.")
        return self.world
        
    def translateWorld(self, scene):
        world = M3GWorld()

        # Background
        world.background = M3GBackground()
        blWorld = scene.world
        
        if blWorld is not None:
            world.background.backgroundColor = self.translateRGBA(blWorld.color, 0)
            if (self.context.scene.m3g_export_props.createAmbientLight and 
                self.context.scene.m3g_export_props.lightingEnabled):
                lLight = M3GLight()
                lLight.mode = lLight.modes['AMBIENT']
                lLight.color = self.translateRGB(blWorld.color)
                self.nodes.append(lLight)

        return world
        
    def translateParenting(self):
        for iNode in self.nodes:
            if iNode.parentBlenderObj is None:
                self.world.children.append(iNode)
            else:
                for jNode in self.nodes:
                    if iNode.parentBlenderObj == jNode.blenderObj:
                        jNode.children.append(iNode)
                        lMatrix = self.calculateChildMatrix(iNode.blenderMatrixWorld, jNode.blenderMatrixWorld)
                        iNode.transform = self.translateMatrix(lMatrix)
                        iNode.hasGeneralTransform = True
                        break
                    
    def calculateChildMatrix(self, child, parent):
        return Matrix(child) @ Matrix(parent).inverted()
    
    def translateArmature(self, obj, meshObj, aSkinnedMesh):
        print("translate Armature ...")
        armature = obj.data
        
        mGroup = M3GGroup()
        self.translateCore(obj, mGroup)
        aSkinnedMesh.skeleton = mGroup
        mGroup.transform = self.translateMatrix(
            self.calculateChildMatrix(obj.matrix_world, meshObj.matrix_world))
        
        # Bones
        for bone in armature.bones:
            mBone = M3GBone()
            mBone.transformNode = M3GGroup()
            self.translateCore(bone, mBone.transformNode)
            
            if bone.parent:
                mBone.transformNode.transform = self.translateMatrix(
                    self.calculateChildMatrix(bone.matrix_local, bone.parent.matrix_local))
            mBone.weight = bone.m3g_weight if hasattr(bone, 'm3g_weight') else 1.0
            aSkinnedMesh.bones[bone.name] = mBone
            
        rootBone = []  # Copy Child-Parent-Structure
        for bone in armature.bones:
            mBone = aSkinnedMesh.bones[bone.name]
            if not bone.parent: 
                rootBone.append(mBone)
            if bone.children:
                for childBone in bone.children:
                    mChildBone = aSkinnedMesh.bones[childBone.name]
                    mBone.transformNode.children.append(mChildBone.transformNode)
                    
        for rbone in rootBone:
            aSkinnedMesh.skeleton.children.append(rbone.transformNode)
        
        # VertexGroups - Skinning
        if meshObj.vertex_groups:
            for boneName in aSkinnedMesh.bones.keys():
                verts = []
                for i, v in enumerate(meshObj.data.vertices):
                    for g in v.groups:
                        if g.group == meshObj.vertex_groups[boneName].index:
                            verts.append(i)
                            break
                aSkinnedMesh.bones[boneName].setVerts(verts)
        
        # Action
        self.translateAction(obj, aSkinnedMesh)
        aSkinnedMesh.addSecondBone()    
        
    def translateAction(self, armatureObj, aSkinnedMesh):
        action = armatureObj.animation_data.action if armatureObj.animation_data else None
        if action is None: 
            return
        
        print("tranlating Action ...")
        if self.context.scene.m3g_export_props.exportAllActions:
            lArmatureID = self.translateUserID(armatureObj.data.name)
            print(f"armatureID {lArmatureID} {armatureObj}")
            
            for a in bpy.data.actions:
                (lArmatureActionID, lEndFrame, lActionID) = self.translateActionName(a.name)
                if lArmatureActionID == lArmatureID:
                    mController = self.translateActionIPOs(a, aSkinnedMesh, lEndFrame)
                    mController.userID = lActionID
        else:
            self.translateActionIPOs(action, aSkinnedMesh)
            
    def translateActionIPOs(self, aIpo, aM3GObject, aM3GAnimContr=None, aEndFrame=0):    
        types = ['location', 'rotation_euler', 'scale']
        
        for type in types:
            if type in aIpo.fcurves:
                self.translateIpoCurve(aIpo, aM3GObject, type, aM3GAnimContr, aEndFrame)

    def translateIpoCurve(self, aIpo, aM3GObject, aCurveType, aM3GAnimContr, aEndFrame=0):
        if aEndFrame == 0: 
            lEndFrame = self.context.scene.frame_end
        else:
            lEndFrame = aEndFrame
            
        lTimePerFrame = 1.0 / self.context.scene.render.fps * 1000 
        
        lCurveX = aIpo.fcurves.find(aCurveType, index=0)
        lCurveY = aIpo.fcurves.find(aCurveType, index=1)
        lCurveZ = aIpo.fcurves.find(aCurveType, index=2)
        
        if aCurveType == 'rotation_quaternion':
            lCurveW = aIpo.fcurves.find(aCurveType, index=3)
        
        if aCurveType == 'rotation_euler' or aCurveType == 'rotation_quaternion':
            lTrackType = M3GAnimationTrack.ORIENTATION
            lNumComponents = 4
            lCurveFactor = 10 if aCurveType == 'rotation_euler' else 1  # 45 Degrees = 4,5
            lInterpolation = M3GKeyframeSequence.SLERP if aCurveType == 'rotation_quaternion' else None
        elif aCurveType == 'scale':
            lTrackType = M3GAnimationTrack.SCALE
            lNumComponents = 3
            lCurveFactor = 1
        else:
            lTrackType = M3GAnimationTrack.TRANSLATION
            lNumComponents = 3
            lCurveFactor = 1
            
        mSequence = M3GKeyframeSequence(len(lCurveX.keyframe_points),
                                       lNumComponents,
                                       lCurveX.interpolation,
                                       lInterpolation)

        mSequence.duration = lEndFrame * lTimePerFrame
        mSequence.setRepeatMode(lCurveX.extrapolation)
        
        lIndex = 0
        for iPoint in lCurveX.keyframe_points:
            lPoint = iPoint.co
            
            if aCurveType == 'rotation_euler':
                lPointList = [
                    lPoint[1] * lCurveFactor,
                    lCurveY.evaluate(lPoint[0]) * lCurveFactor,
                    lCurveZ.evaluate(lPoint[0]) * lCurveFactor
                ]
                lQuat = Euler(lPointList).to_quaternion()
                lPointList = [lQuat.x, lQuat.y, lQuat.z, lQuat.w]
            elif aCurveType == 'rotation_quaternion':
                lPointList = [
                    lPoint[1] * lCurveFactor,
                    lCurveY.evaluate(lPoint[0]) * lCurveFactor,
                    lCurveZ.evaluate(lPoint[0]) * lCurveFactor,
                    lCurveW.evaluate(lPoint[0]) * lCurveFactor
                ]
            else:
                lPointList = [
                    lPoint[1] * lCurveFactor,
                    lCurveY.evaluate(lPoint[0]) * lCurveFactor,
                    lCurveZ.evaluate(lPoint[0]) * lCurveFactor
                ]
        
            mSequence.setKeyframe(lIndex,
                                lPoint[0] * lTimePerFrame, 
                                lPointList)
            lIndex += 1
            
        mSequence.validRangeFirst = 0 
        mSequence.validRangeLast = lIndex - 1  
        
        mTrack = M3GAnimationTrack(mSequence, lTrackType)
        aM3GObject.animationTracks.append(mTrack)
        if aM3GAnimContr is None:  
            aM3GAnimContr = M3GAnimationController()
        mTrack.animationController = aM3GAnimContr

    def translateEmpty(self, obj):
        print("translate empty ...")
        mGroup = M3GGroup()
        self.translateToNode(obj, mGroup)
            
    def translateCamera(self, obj):
        print("translate camera ...")
        camera = obj.data
        if camera.type != 'PERSP':
            print("Only perscpectiv cameras will work korrekt")
            return
            
        mCamera = M3GCamera()
        mCamera.projectionType = mCamera.PERSPECTIVE
        mCamera.fovy = camera.angle * 180 / 3.1415926  # Convert to degrees
        mCamera.AspectRatio = camera.sensor_width / camera.sensor_height
        mCamera.near = camera.clip_start
        mCamera.far = camera.clip_end
        self.translateToNode(obj, mCamera)
        self.world.activeCamera = mCamera  # Last one is always the active one
    
    def translateMaterials(self, aMaterial, aMesh, aMatIndex, createNormals, createUvs):
        print("translate materials ...")
        
        mAppearance = M3GAppearance()
        
        if createNormals:
            mMaterial = M3GMaterial()
            mMaterial.name = aMaterial.name
            mMaterial.diffuseColor = self.translateRGBA(aMaterial.diffuse_color, aMaterial.diffuse_color[3])
            mAppearance.material = mMaterial

        if createUvs:
            # Nova abordagem para encontrar imagens de textura - verifica os nós do material
            lImage = None
            if aMaterial.use_nodes:
                for node in aMaterial.node_tree.nodes:
                    if node.type == 'TEX_IMAGE' and node.image is not None:
                        lImage = node.image
                        break
            
            if lImage is None:
                # Tenta encontrar a imagem nas UVs (maneira antiga, apenas para compatibilidade)
                for iface in aMesh.polygons:
                    if iface.material_index == aMatIndex:
                        for uv_layer in aMesh.uv_layers:
                            for loop in iface.loop_indices:
                                # Verifica se há uma imagem associada à UV
                                # Nota: No Blender 3.6, os loops UV não têm mais a propriedade 'image' diretamente
                                # Esta parte pode não funcionar e é mantida apenas para compatibilidade
                                try:
                                    if hasattr(aMesh.uv_layers.active.data[loop], 'image') and aMesh.uv_layers.active.data[loop].image is not None:
                                        lImage = aMesh.uv_layers.active.data[loop].image
                                        break
                                except:
                                    pass
                            if lImage is not None:
                                break
                        if lImage is not None:
                            break
                        
            if lImage is None:
                print(f"Warning: No image found for uv-texture in mesh {aMesh.name}")
                return mAppearance

            # M3G requires textures to have power-of-two dimensions
            width, height = lImage.size
            powerWidth = 1
            while powerWidth < width:
                powerWidth *= 2
            powerHeight = 1
            while powerHeight < height:
                powerHeight *= 2
            if powerWidth != width or powerHeight != height:
                print(f"Warning: Image {lImage.filepath} dimensions are not power-of-two! Texture will not be exported.")
                return mAppearance
                
            # ImageFactory reuses existing images
            mImage = ImageFactory.getImage(lImage, self.context.scene.m3g_export_props.textureExternal)
            mTexture = M3GTexture2D(mImage)
            mAppearance.textures.append(mTexture)

        mPolygonMode = M3GPolygonMode()
        mPolygonMode.perspectiveCorrectionEnabled = self.context.scene.m3g_export_props.perspectiveCorrection
        
        if not aMesh.use_mirror_x:
            mPolygonMode.culling = M3GPolygonMode.CULL_BACK
        else:
            mPolygonMode.culling = M3GPolygonMode.CULL_NONE 
            
        if self.context.scene.m3g_export_props.smoothShading:
            mPolygonMode.shading = M3GPolygonMode.SHADE_SMOOTH
        else:
            mPolygonMode.shading = M3GPolygonMode.SHADE_FLAT
        
        mAppearance.polygonMode = mPolygonMode
        
        return mAppearance

    def translateMesh(self, obj):
        print(f"translate mesh ... {str(obj)}")

        # Mesh data
        mesh = obj.data
        if len(mesh.polygons) <= 0:  # no need to process empty meshes
            print(f"Empty mesh {str(obj)} not processed.")
            return
            
        vertexBuffer = M3GVertexBuffer()
        positions = M3GVertexArray(3, 2)  # 3 coordinates - 2 bytes
        if self.context.scene.m3g_export_props.autoscaling: 
            positions.useMaxPrecision(mesh.vertices)
            
        indexBuffers = []
        appearances = []
        print(f"{len(mesh.materials)} material(s) found.")
        
        # Texture coordinates
        createUvs = False
        if (self.context.scene.m3g_export_props.textureEnabled and 
            mesh.uv_layers.active is not None):
            for material in mesh.materials:
                if material is not None and material.use_nodes:
                    for node in material.node_tree.nodes:
                        if node.type == 'TEX_IMAGE':
                            createUvs = True
                            break
                    if createUvs:
                        break

        if createUvs:
            if self.context.scene.m3g_export_props.autoscaling:
                uvCoordinates = M3GVertexArray(2, 2, True, True)  # 2 coordinates - 2 bytes - autoscaling
            else:
                uvCoordinates = M3GVertexArray(2, 2)  # 2 coordinates - 2 bytes
                uvCoordinates.bias[0] = 0.5
                uvCoordinates.bias[1] = 0.5
                uvCoordinates.bias[2] = 0.5
                uvCoordinates.scale = 1.0 / 65535.0
        else:
            uvCoordinates = None

        # Normals            
        createNormals = False    
        if self.context.scene.m3g_export_props.lightingEnabled:
            for material in mesh.materials:
                if material is not None and not material.use_nodes:  # Changed from use_shadeless
                    createNormals = True
                    break

        if createNormals:
            normals = M3GVertexArray(3, 1)  # 3 coordinates - 1 byte
        else:
            normals = None
        
        # Create a submesh for each material
        for materialIndex, material in enumerate(mesh.materials):
            faces = [face for face in mesh.polygons if face.material_index == materialIndex]
            if len(faces) >= 0:
                appearance = self.translateMaterials(material, mesh, materialIndex, createNormals, createUvs)
                if appearance is not None:  # Only add if material translation was successful
                    indexBuffers.append(self.translateFaces(faces, positions, normals, uvCoordinates, createNormals, createUvs, mesh))
                    appearances.append(appearance)
                
        # If the above didn't result in any IndexBuffer (e.g. there's no material), 
        # write a single IndexBuffer with all faces and a default Appearance
        if len(indexBuffers) == 0: 
            indexBuffers.append(self.translateFaces(mesh.polygons, positions, normals, uvCoordinates, createNormals, createUvs, mesh))
            appearances.append(M3GAppearance())

        vertexBuffer.setPositions(positions)
        if createNormals: 
            vertexBuffer.normals = normals
        if createUvs: 
            vertexBuffer.texCoordArrays.append(uvCoordinates)

        parent = obj.parent
        if parent is not None and parent.type == 'ARMATURE':
            mMesh = M3GSkinnedMesh(vertexBuffer, indexBuffers, appearances)
            self.translateArmature(parent, obj, mMesh)
        else:
            mMesh = M3GMesh(vertexBuffer, indexBuffers, appearances)
            
        self.translateToNode(obj, mMesh)
        
        # Do Animation
        self.translateObjectIpo(obj, mMesh)  
        
    def translateFaces(self, faces, positions, normals, uvCoordinates, createNormals, createUvs, mesh):
        """Translates a list of faces into vertex data and triangle strips."""
        indices = [0, 0, 0, 0]
        triangleStrips = M3GTriangleStripArray()
        
        uv_layer = mesh.uv_layers.active if createUvs else None
        
        for face in faces:
            for vertexIndex, vertex in enumerate(face.vertices):
                # Find candidates for sharing (vertices with same Blender ID)
                vertexCandidateIds = [int(k) for k, v in positions.blenderIndexes.items() if v == vertex]

                # Check normal
                if createNormals and not face.use_smooth:
                    # For solid faces, a vertex can only be shared if the face normal is 
                    # the same as the normal of the shared vertex
                    for candidateId in vertexCandidateIds[:]:
                        for j in range(3):
                            if face.normal[j] * 127 != normals.components[candidateId * 3 + j]:
                                vertexCandidateIds.remove(candidateId)
                                break

                # Check texture coordinates
                if createUvs and uv_layer is not None:
                    # If texture coordinates are required, a vertex can only be shared if the 
                    # texture coordinates match
                    uv_data = uv_layer.data[face.loop_indices[vertexIndex]].uv
                    for candidateId in vertexCandidateIds[:]:
                        s = int((uv_data[0] - 0.5) * 65535)
                        t = int((0.5 - uv_data[1]) * 65535)
                        if (s != uvCoordinates.components[candidateId * 2 + 0] or 
                            t != uvCoordinates.components[candidateId * 2 + 1]):
                            vertexCandidateIds.remove(candidateId)

                if len(vertexCandidateIds) > 0:
                    # Share the vertex
                    indices[vertexIndex] = vertexCandidateIds[0]
                else:
                    # Create new vertex
                    positions.append(mesh.vertices[vertex], vertex)
                    indices[vertexIndex] = len(positions.components) // 3 - 1

                    # Normal
                    if createNormals:
                        for j in range(3):
                            if face.use_smooth:
                                normals.append(int(mesh.vertices[vertex].normal[j] * 127))  # vertex normal
                            else:
                                normals.append(int(face.normal[j] * 127))  # face normal

                    # Texture coordinates
                    if createUvs and uv_layer is not None:
                        uv_data = uv_layer.data[face.loop_indices[vertexIndex]].uv
                        if self.context.scene.m3g_export_props.autoscaling:
                            uvCoordinates.append(uv_data[0])
                            uvCoordinates.append(uv_data[1])
                        else:
                            uvCoordinates.append(int((uv_data[0] - 0.5) * 65535))
                            # Reverse t coordinate because M3G uses a different 2D coordinate system than Blender
                            uvCoordinates.append(int((0.5 - uv_data[1]) * 65535))

            # IndexBuffer
            triangleStrips.stripLengths.append(len(face.vertices)) 
            if len(face.vertices) > 3:
                triangleStrips.indices += [indices[1], indices[2], indices[0], indices[3]]  # quad
            else:
                triangleStrips.indices += [indices[0], indices[1], indices[2]]  # tri
                
        return triangleStrips
        
    def translateObjectIpo(self, obj, aM3GObject):
        if obj.animation_data is None or obj.animation_data.action is None:
            return  # No Ipo available
            
        print("translate Ipo ...")
        lIpo = obj.animation_data.action
        self.translateIpo(lIpo, aM3GObject)
        
    def translateIpo(self, aIpo, aM3GObject, aM3GAnimContr=None, aEndFrame=0):    
        types = ['location', 'rotation_euler', 'scale']
        
        for type in types:
            if type in aIpo.fcurves:
                self.translateIpoCurve(aIpo, aM3GObject, type, aM3GAnimContr, aEndFrame)

    def translateIpoCurve(self, aIpo, aM3GObject, aCurveType, aM3GAnimContr, aEndFrame=0):
        if aEndFrame == 0: 
            lEndFrame = self.context.scene.frame_end
        else:
            lEndFrame = aEndFrame
            
        lTimePerFrame = 1.0 / self.context.scene.render.fps * 1000 
        
        lCurveX = aIpo.fcurves.find(aCurveType, index=0)
        lCurveY = aIpo.fcurves.find(aCurveType, index=1)
        lCurveZ = aIpo.fcurves.find(aCurveType, index=2)
        
        if aCurveType == 'rotation_quaternion':
            lCurveW = aIpo.fcurves.find(aCurveType, index=3)
        
        if aCurveType == 'rotation_euler' or aCurveType == 'rotation_quaternion':
            lTrackType = M3GAnimationTrack.ORIENTATION
            lNumComponents = 4
            lCurveFactor = 10 if aCurveType == 'rotation_euler' else 1  # 45 Degrees = 4,5
            lInterpolation = M3GKeyframeSequence.SLERP if aCurveType == 'rotation_quaternion' else None
        elif aCurveType == 'scale':
            lTrackType = M3GAnimationTrack.SCALE
            lNumComponents = 3
            lCurveFactor = 1
        else:
            lTrackType = M3GAnimationTrack.TRANSLATION
            lNumComponents = 3
            lCurveFactor = 1
            
        mSequence = M3GKeyframeSequence(len(lCurveX.keyframe_points),
                                       lNumComponents,
                                       lCurveX.interpolation,
                                       lInterpolation)

        mSequence.duration = lEndFrame * lTimePerFrame
        mSequence.setRepeatMode(lCurveX.extrapolation)
        
        lIndex = 0
        for iPoint in lCurveX.keyframe_points:
            lPoint = iPoint.co
            
            if aCurveType == 'rotation_euler':
                lPointList = [
                    lPoint[1] * lCurveFactor,
                    lCurveY.evaluate(lPoint[0]) * lCurveFactor,
                    lCurveZ.evaluate(lPoint[0]) * lCurveFactor
                ]
                lQuat = Euler(lPointList).to_quaternion()
                lPointList = [lQuat.x, lQuat.y, lQuat.z, lQuat.w]
            elif aCurveType == 'rotation_quaternion':
                lPointList = [
                    lPoint[1] * lCurveFactor,
                    lCurveY.evaluate(lPoint[0]) * lCurveFactor,
                    lCurveZ.evaluate(lPoint[0]) * lCurveFactor,
                    lCurveW.evaluate(lPoint[0]) * lCurveFactor
                ]
            else:
                lPointList = [
                    lPoint[1] * lCurveFactor,
                    lCurveY.evaluate(lPoint[0]) * lCurveFactor,
                    lCurveZ.evaluate(lPoint[0]) * lCurveFactor
                ]
        
            mSequence.setKeyframe(lIndex,
                                lPoint[0] * lTimePerFrame, 
                                lPointList)
            lIndex += 1
            
        mSequence.validRangeFirst = 0 
        mSequence.validRangeLast = lIndex - 1  
        
        mTrack = M3GAnimationTrack(mSequence, lTrackType)
        aM3GObject.animationTracks.append(mTrack)
        if aM3GAnimContr is None:  
            aM3GAnimContr = M3GAnimationController()
        mTrack.animationController = aM3GAnimContr

    def translateLamp(self, obj):
        print("translate lamp ...")
        lamp = obj.data
        
        # Type
        if lamp.type not in ['POINT', 'SPOT', 'SUN']:
            print("INFO: Type of light is not supported. See documentation")
            return  # create not light; type not supported
            
        mLight = M3GLight()
        if lamp.type == 'POINT':
            mLight.mode = mLight.modes['OMNI']
        elif lamp.type == 'SPOT':
            mLight.mode = mLight.modes['SPOT']
        elif lamp.type == 'SUN':
            mLight.mode = mLight.modes['DIRECTIONAL']
            
        # Attenuation (OMNI,SPOT):
        if lamp.type in ['POINT', 'SPOT']:
            mLight.attenuationConstant = 0.0
            mLight.attenuationLinear = 2.0 / lamp.distance 
            mLight.attenuationQuadratic = 0.0 
            
        # Color
        mLight.color = self.translateRGB(lamp.color)        
        # Energy  
        mLight.intensity = lamp.energy
        # SpotAngle, SpotExponent (SPOT)
        if lamp.type == 'SPOT':
            mLight.spotAngle = lamp.spot_size / 2 
            mLight.spotExponent = lamp.spot_blend 
            
        self.translateToNode(obj, mLight)

    def translateCore(self, obj, node):
        # Name
        node.name = obj.name
        node.userID = self.translateUserID(obj.name)
        # Transform
        if isinstance(obj, bpy.types.Bone):
            node.transform = self.translateMatrix(obj.matrix_local)
        else:
            node.transform = self.translateMatrix(obj.matrix_world)
        node.hasGeneralTransform = True
        
    def translateToNode(self, obj, node):
        self.translateCore(obj, node)
        # Nodes
        self.nodes.append(node)
        # Link to Blender Object
        node.blenderObj = obj
        node.blenderMatrixWorld = obj.matrix_world
        lparent = None
        if obj.parent is not None:
            if obj.parent.type != 'ARMATURE':
                lparent = obj.parent
            else:
                if (obj.parent.parent is not None and 
                    obj.parent.parent.type != 'ARMATURE'):
                    lparent = obj.parent.parent
        node.parentBlenderObj = lparent
        
    def translateUserID(self, name):
        id = 0
        start = name.find('#')
        
        # Use digits that follow the # sign for id
        if start != -1:
            start += 1
            end = start
            for char in name[start:]:
                if char.isdigit():
                    end += 1
                else:
                    break
                    
            if end > start:
                id = int(name[start:end])
        
        return id
        
    def translateLoc(self, aLocX, aLocY, aLocZ):
        return M3GVector3D(aLocX, aLocY, aLocZ)
        
    def translateRGB(self, color):
        return M3GColorRGB(int(color[0] * 255),
                          int(color[1] * 255), 
                          int(color[2] * 255))
    
    def translateRGBA(self, color, alpha):
        return M3GColorRGBA(int(color[0] * 255),
                          int(color[1] * 255), 
                          int(color[2] * 255),
                          int(alpha * 255))
    
    def translateMatrix(self, aPyMatrix):
        lMatrix = M3GMatrix()
        lMatrix.elements[0] = aPyMatrix[0][0]
        lMatrix.elements[1] = aPyMatrix[1][0]
        lMatrix.elements[2] = aPyMatrix[2][0]
        lMatrix.elements[3] = aPyMatrix[3][0]
        lMatrix.elements[4] = aPyMatrix[0][1]
        lMatrix.elements[5] = aPyMatrix[1][1]
        lMatrix.elements[6] = aPyMatrix[2][1]
        lMatrix.elements[7] = aPyMatrix[3][1]
        lMatrix.elements[8] = aPyMatrix[0][2]
        lMatrix.elements[9] = aPyMatrix[1][2]
        lMatrix.elements[10] = aPyMatrix[2][2]
        lMatrix.elements[11] = aPyMatrix[3][2]
        lMatrix.elements[12] = aPyMatrix[0][3]
        lMatrix.elements[13] = aPyMatrix[1][3]
        lMatrix.elements[14] = aPyMatrix[2][3]
        lMatrix.elements[15] = aPyMatrix[3][3]
        return lMatrix
# ---- Exporter ---------------------------------------------------------------- #
class M3GExporter:
    def __init__(self, context, aWriter): 
        self.context = context
        self.writer = aWriter

    def start(self):
        print("Info: starting export ...")
        Translator = M3GTranslator(self.context)
        world = Translator.start()
        
        exportList = self.createDeepSearchList(world)
        externalReferences = [element for element in exportList if isinstance(element, M3GExternalReference)]
        exportList = [element for element in exportList if not isinstance(element, M3GExternalReference)]
        
        # 1 is reservated for HeaderObject
        i = 1 
        
        # Next are the external references
        for element in externalReferences:
            i += 1
            element.id = i
            print(f"object {element.id} {element}")
            
        # And the standard scene objects
        for element in exportList:
            i += 1
            element.id = i
            print(f"object {element.id} {element}")
            
        self.writer.writeFile(world, exportList, externalReferences)
        
        print("Ready!")

    def createDeepSearchList(self, aWorld):
        return aWorld.searchDeep([])

# ---- Writer ---------------------------------------------------------------- #   
class JavaWriter:
    def __init__(self, aFilename):
        self.filename = aFilename
        self.classname = os.path.basename(aFilename)
        self.classname = self.classname[:-5]  # without extention ".java"
        self.outFile = open(aFilename, "w")
        
    def write(self, tab, zeile=""):
        print("\t" * tab + zeile, file=self.outFile)

    def writeFile(self, aWorld, aExportList, externalReferences):
        self.world = aWorld
        self.writeHeader()
        for element in aExportList:
            element.writeJava(self, True)
        self.writeFooter()
        self.outFile.close()
        
    def writeHeader(self):
        self.write(0, "import javax.microedition.lcdui.Image;")
        self.write(0, "import javax.microedition.m3g.*;")
        self.write(0, f"public final class {self.classname} {{")
        self.write(1, "public static World getRoot(Canvas3D aCanvas) {")
          
    def writeFooter(self):
        self.write(1)
        self.write(1, f"return BL{self.world.id};")
        self.write(0, "}}")
        
    def writeList(self, alist, numberOfElementsPerLine=12, aType=""):
        line = ""
        lastLine = ""
        counter = 0
        for element in alist:
            if counter != 0:
                line = line + "," + str(element) + aType
            else:
                line = str(element) + aType
            counter += 1
            if counter == numberOfElementsPerLine:
                if len(lastLine) > 0:
                    self.write(3, lastLine + ",")
                lastLine = line
                line = ""
                counter = 0
        if len(lastLine) > 0:
            if len(line) > 0:
                self.write(3, lastLine + ",")
            else:
                self.write(3, lastLine)
        if len(line) > 0: 
            self.write(3, line)
    
    def writeClass(self, aName, aM3GObject):
        self.write(2)
        self.write(2, f"//{aName}:{aM3GObject.name}")

class M3GSectionObject:
    def __init__(self, aObject):
        self.ObjectType = aObject.ObjectType
        self.data = aObject.getData()
        self.length = aObject.getDataLength()
    
    def getData(self):
        data = struct.pack('<BI', self.ObjectType, self.length)
        data += self.data
        return data
    
    def getDataLength(self):
        return struct.calcsize('<BI') + self.length
        
class M3GSection:
    def __init__(self, aObjectList, compressed=False):
        self.CompressionScheme = 0
        self.TotalSectionLength = 0
        self.UncompressedLength = 0
        self.Objects = b''
        
        for element in aObjectList:
            lObject = M3GSectionObject(element)
            self.Objects += lObject.getData()
            self.UncompressedLength += lObject.getDataLength()
            
        self.TotalSectionLength = struct.calcsize('<BIII') + self.UncompressedLength
    
    def getData(self):
        data = struct.pack('<BII', 
                          self.CompressionScheme,
                          self.TotalSectionLength,
                          self.UncompressedLength)
        data += self.Objects
        self.Checksum = self.ownAdler32(data)
        print(f"Checksum {self.Checksum}")
        return data + struct.pack('<I', self.Checksum)
    
    def ownAdler32(self, data):
        s1 = 1  # uint32_t
        s2 = 0  # uint32_t
        for n in data:
            s1 = (s1 + n) % 65521
            s2 = (s2 + s1) % 65521
        return (s2 << 16) + s1
    
    def getLength(self):
        return self.TotalSectionLength
        
    def write(self, aFile):
        print("Write Section..")
        print(f"TotalSectionLength: {str(self.TotalSectionLength)}")
        aFile.write(self.getData())
            
class M3GFileIdentifier:
    def __init__(self):
        self.data = [
            0xAB, 0x4A, 0x53, 0x52, 0x31, 0x38, 0x34,
            0xBB, 0x0D, 0x0A, 0x1A, 0x0A
        ]
    
    def write(self, aFile):
        aFile.write(bytes(self.data))
        
    def getLength(self):
        return len(self.data)
        
class M3GWriter:
    def __init__(self, aFilename):
        self.FileName = aFilename
    
    def writeFile(self, aWorld, aExportList, externalReferences):
        print("M3G file writing ..")
        
        fileIdentifier = M3GFileIdentifier()
        
        fileHeaderObject = M3GHeaderObject()
        section0 = M3GSection([fileHeaderObject])
        sectionN = M3GSection(aExportList)
        
        length = fileIdentifier.getLength()
        length += section0.getLength()
        length += sectionN.getLength()
        
        if len(externalReferences) != 0:
            section1 = M3GSection(externalReferences)
            length += section1.getLength()
            fileHeaderObject.hasExternalReferences = True
        
        fileHeaderObject.TotalFileSize = length 
        fileHeaderObject.ApproximateContentSize = length
        section0 = M3GSection([fileHeaderObject])
       
        with open(self.FileName, 'wb') as output:
            fileIdentifier.write(output)
            section0.write(output)
            if len(externalReferences) != 0:
                section1.write(output)
            sectionN.write(output)

        print("M3G file written.")

# ---- Operator and UI -------------------------------------------------------- #
class M3GExportProperties(bpy.types.PropertyGroup):
    textureEnabled: BoolProperty(
        name="Texturing Enabled",
        description="Switches on/off export of textures and texture coordinates",
        default=True
    )
    
    textureExternal: BoolProperty(
        name="Texturing External",
        description="References external files for textures",
        default=False
    )
    
    lightingEnabled: BoolProperty(
        name="Lighting Enabled",
        description="Turns on/off export of lights and normals completely",
        default=True
    )
    
    createAmbientLight: BoolProperty(
        name="Ambient Light",
        description="Inserts an extra light object for ambient light",
        default=False
    )
    
    autoscaling: BoolProperty(
        name="Autoscaling",
        description="Uses maximum precision for vertex positions",
        default=True
    )
    
    perspectiveCorrection: BoolProperty(
        name="Persp. Correction",
        description="Sets perspective correction flag",
        default=False
    )
    
    smoothShading: BoolProperty(
        name="Smooth Shading",
        description="Sets smooth shading flag",
        default=True
    )
    
    exportAllActions: BoolProperty(
        name="All Armature Actions",
        description="Exports all actions for armatures",
        default=False
    )
    
    exportAsJava: BoolProperty(
        name="As Java Source",
        description="Exports scene as Java source code",
        default=False
    )
    
    exportVersion2: BoolProperty(
        name="M3G Version 2.0",
        description="Exports M3G Version 2.0 File",
        default=False
    )
    
    exportGamePhysics: BoolProperty(
        name="Game Physics",
        description="Includes Game Physics infos for NOPE in export",
        default=False
    )

class M3GExportOperator(Operator, ExportHelper):
    """Export to M3G format (JSR-184)"""
    bl_idname = "export_scene.m3g"
    bl_label = "Export M3G"
    bl_options = {'PRESET'}

    filename_ext = ".m3g"
    filter_glob: StringProperty(default="*.m3g", options={'HIDDEN'})

    def execute(self, context):
        if not self.filepath:
            raise Exception("filepath not set")

        if context.scene.m3g_export_props.exportAsJava:
            exporter = M3GExporter(context, JavaWriter(self.filepath))
        else:
            exporter = M3GExporter(context, M3GWriter(self.filepath))
            
        exporter.start()
        return {'FINISHED'}

class M3G_PT_export_main(Panel):
    bl_space_type = 'FILE_BROWSER'
    bl_region_type = 'TOOL_PROPS'
    bl_label = ""
    bl_parent_id = "FILE_PT_operator"
    bl_options = {'HIDE_HEADER'}

    @classmethod
    def poll(cls, context):
        sfile = context.space_data
        operator = sfile.active_operator
        return operator.bl_idname == "EXPORT_SCENE_OT_m3g"

    def draw(self, context):
        layout = self.layout
        props = context.scene.m3g_export_props

        layout.use_property_split = True
        layout.use_property_decorate = False

        col = layout.column(heading="Texturing")
        col.prop(props, "textureEnabled")
        col.prop(props, "textureExternal")

        col = layout.column(heading="Lighting")
        col.prop(props, "lightingEnabled")
        col.prop(props, "createAmbientLight")

        col = layout.column(heading="Mesh Options")
        col.prop(props, "autoscaling")
        col.prop(props, "perspectiveCorrection")
        col.prop(props, "smoothShading")

        col = layout.column(heading="Posing")
        col.prop(props, "exportAllActions")

        col = layout.column(heading="Export")
        col.prop(props, "exportAsJava")
        col.prop(props, "exportVersion2")
        col.prop(props, "exportGamePhysics")

def menu_func_export(self, context):
    self.layout.operator(M3GExportOperator.bl_idname, text="M3G (.m3g, .java)")

def register():
    bpy.utils.register_class(M3GExportProperties)
    bpy.utils.register_class(M3GExportOperator)
    bpy.utils.register_class(M3G_PT_export_main)
    bpy.types.Scene.m3g_export_props = bpy.props.PointerProperty(type=M3GExportProperties)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)

def unregister():
    bpy.utils.unregister_class(M3GExportProperties)
    bpy.utils.unregister_class(M3GExportOperator)
    bpy.utils.unregister_class(M3G_PT_export_main)
    del bpy.types.Scene.m3g_export_props
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)

if __name__ == "__main__":
    register()

def doSearchDeep(inList, outList):
    for element in inList:
        if element is not None: 
            outList = element.searchDeep(outList)
    return outList

def getId(aObject):
    return 0 if aObject is None else aObject.id

def TriangleNormal(v0, v1, v2):
    return (v1 - v0).cross(v2 - v0)