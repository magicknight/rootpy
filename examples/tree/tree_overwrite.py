#!/usr/bin/env python

"""
This is an example showing how to copy a tree while overwriting one or more of
its branches with new values.
"""

from rootpy.tree import Tree, TreeModel
from rootpy.io import open as ropen
from rootpy.types import *
from random import gauss


# define the model
class Event(TreeModel):

    x = FloatCol()
    y = FloatCol()
    z = FloatCol()
    i = IntCol()

# first create a tree "test" in a file "test.root"
f = ropen("test.root", "recreate")

tree = Tree("test", model=Event)

# fill the tree
for i in xrange(10000):
    tree.x = gauss(.5, 1.)
    tree.y = gauss(.3, 2.)
    tree.z = gauss(13., 42.)
    tree.i = i
    tree.fill()
tree.write()

# Now we want to copy the tree above into a new file while overwriting a branch
f_copy = ropen("test_copy.root", "recreate")

# you may not know the entire model of the original tree but only the branches
# you intend to overwrite, so I am not specifying the model=Event below as an
# example of how to deal with this.
tree_copy = Tree("test_copy")
# if the original tree was not handed to you through rootpy don't forget to:
# from rootpy.utils import asrootpy
# tree = asrootpy(tree)
# Here we specify the buffer for the new tree to use. We use the same buffer as
# the original tree. This creates all the same branches in the new tree but
# their addresses point to the same memory used by the original tree.
tree_copy.set_buffer(
        tree.buffer,
        create_branches=True)

# now loop over the original tree and fill the new tree
for entry in tree:
    # overwrite a branch value
    # this changes the value that will be written to the new tree but leaves the
    # value unchanged in the original tree on disk.
    entry.x = 3.141
    # "entry" is actually the buffer, which is shared between both trees.
    tree_copy.Fill()

# tree_copy is now a copy of tree where the "x" branch has been overwritten with
# a new values
tree_copy.Write()
f_copy.Close()
f.Close()
