import networkx as nx
import matplotlib as mpl
import matplotlib.pyplot as plt
from src.models.train_DRRAA_module import DRRAA
from src.models.train_KAA_module import KAA
import torch
import numpy as np
import netwulf as nw

seed = 1
torch.random.manual_seed(seed)
np.random.seed(seed)

#import data
G = nx.read_gml("data/raw/polblogs/polblogs.gml")
G = G.to_undirected()

if nx.number_connected_components(G) > 1:
    Gcc = sorted(nx.connected_components(G), key=len, reverse=True)
    G = G.subgraph(Gcc[0])
label_map = {x: i for i, x in enumerate(G.nodes)}
reverse_label_map = {i: x for x,i in label_map.items()}
G = nx.relabel_nodes(G, label_map)

kvals = [2]#[2,3,4,5,6]
#kvals = [3]
iter=10000

for k in kvals:
    kaa = KAA(k=k, data=nx.adjacency_matrix(G).todense())
    kaa.train(iterations=1000)

#define model
    RAA = DRRAA(d=2, k=k, data=G, data_type='networkx',link_pred=True, sample_size=0.8, init_Z=kaa.S.detach())
    RAA.train(iterations=iter, LR=0.01, print_loss=False, scheduling=False, early_stopping=0.8)
    raa_auc, fpr, tpr = RAA.link_prediction()

    #get colorlist
    color_list = ["303638","f0c808","5d4b20","469374","9341b3","e3427d","e68653","ebe0b0","edfbba","ffadad","ffd6a5","fdffb6","caffbf","9bf6ff","a0c4ff","bdb2ff","ffc6ff","fffffc"]
    color_list = ["#"+i.lower() for i in color_list]
    color_map = [color_list[14] if G.nodes[i]['value'] == 0 else color_list[5] for i in G.nodes()]

    #draw graph
    fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(10,10), dpi=100)
    d = dict(G.degree)
    embeddings, archetypes = RAA.get_embeddings()
    archetypal_nodes = RAA.archetypal_nodes()
    pos_map = dict(list(zip(G.nodes(), list(embeddings))))
    nx_pos_map = nx.spring_layout(G)
    nx.draw_networkx_nodes(G, pos=nx_pos_map, ax = ax1, node_color=color_map, alpha=.9, node_size=[v for v in d.values()])
    nx.draw_networkx_edges(G, pos=nx_pos_map, ax = ax1, alpha=.2)
    nx.draw_networkx_nodes(G, pos=pos_map, ax=ax2, node_color=color_map, alpha=.9, node_size=[v for v in d.values()])
    nx.draw_networkx_edges(G, pos=pos_map, ax=ax2, alpha=.1)

    ax2.scatter(archetypes[0, :], archetypes[1, :], marker='^', c='black', label="Archetypes", s=80)
    for i in archetypal_nodes:
        ax2.annotate(reverse_label_map[int(i)], 
                        xy=(embeddings[int(i),:]),
                        xytext=(embeddings[int(i),:])*1.005,
                        bbox=dict(boxstyle="round4",
                        fc=color_map[int(i)],
                        ec="black",
                        lw=2),
                        arrowprops=dict(arrowstyle="-|>",
                                    connectionstyle="arc3,rad=-0.2",
                                    fc="w"))
    ax2.legend()
    ax1.set_title('Networkx\'s Spring Layout')
    ax2.set_title('RAA\'s Embeddings')
    ax3.plot(fpr, tpr, '#C4000D', label='AUC = %0.2f' % raa_auc)
    ax3.plot([0, 1], [0, 1], 'b--', label='random')
    ax3.legend(loc='lower right')
    ax3.set_xlabel("False positive rate")
    ax3.set_ylabel("True positive rate")
    ax3.set_title("AUC")
    RAA.decision_boundary_linear("value", ax4)
    #ax4.plot([i for i in range(1,iter+1)], RAA.losses, c="#C4000D")
    #ax4.set_title("Loss")
    plt.savefig(f"show_polblogs_kaa_init_{k}.png", dpi=100)

#plt.show()

    print(RAA.KNeighborsClassifier("value"))
    print(RAA.logistic_regression("value"))
